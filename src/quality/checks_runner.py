"""
Observability Pillars Orchestrator

Runs the observability pillars (freshness, volume, schema, distribution) across all cataloged
tables using the snapshots accumulated in reports/quality_history/, then:

- evaluates the SLOs of config/slo.yaml from those same findings (one evaluation per pair);
- for each anomaly: sends an alert whose severity follows the table criticality (catalog) and
  registers an incident listing the downstream tables impacted via lineage;
- skips anomalies already registered for the same snapshot, so re-running the checks on
  unchanged data never opens duplicate incidents.

Usage:
    poetry run python -m src.quality.checks_runner
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src.config import PROJECT_ROOT, load_catalog, load_settings
from src.incidents.incident_log import find_incident, load_incidents, register_incident
from src.ingestion.contract_validator import load_contract
from src.monitoring.alerting import Severity, send_alert
from src.monitoring.history_store import load_history
from src.quality.pillars.base import PillarFinding
from src.quality.pillars.lineage import Graph, build_lineage_graph, downstream_impact
from src.quality.sli_slo import SLOResult, evaluate_slos, evaluate_table, load_slos

SEVERITY_BY_CRITICALITY: dict[str, Severity] = {"high": "critical", "medium": "warning", "low": "info"}


def occurrence_time(finding: PillarFinding, snapshot_at: str, params: dict[str, Any]) -> datetime:
    """
    Estimates when the problem started, to compute a meaningful TTD:
    - freshness: when the delay crossed the SLO (last ingestion + max_delay_hours);
    - other pillars: when the anomalous data landed (the snapshot's ingestion time).
    """
    landed_at = datetime.fromisoformat(snapshot_at)
    if finding.pillar == "freshness":
        return landed_at + timedelta(hours=params["max_delay_hours"])
    return landed_at


def handle_anomaly(
    finding: PillarFinding,
    table_meta: dict[str, Any],
    snapshot_at: str,
    params: dict[str, Any],
    graph: Graph,
    known_incidents: list[dict[str, Any]],
    alerts_dir: Path,
    incidents_dir: Path,
) -> None:
    """Alerts and registers an incident for an anomaly, unless it is already registered."""
    existing = find_incident(known_incidents, finding.table, finding.pillar, snapshot_at)
    if existing is not None:
        print(f"  [{finding.pillar}] already registered as incident {existing['incident_id']} - no new alert.")
        return

    affected = downstream_impact(finding.table, graph)
    severity = SEVERITY_BY_CRITICALITY.get(table_meta.get("criticality"), "warning")
    impact = ", ".join(affected) if affected else "none"

    send_alert(
        message=f"[{finding.pillar.upper()}] {finding.table}: {finding.detail} | downstream impact: {impact}",
        severity=severity,
        alerts_dir=alerts_dir,
    )
    incident = register_incident(
        table=finding.table,
        pillar=finding.pillar,
        detail=finding.detail,
        affected_tables=affected,
        incidents_dir=incidents_dir,
        occurred_at=occurrence_time(finding, snapshot_at, params),
        severity=severity,
        snapshot_at=snapshot_at,
    )
    known_incidents.append(incident.as_dict())


def evaluate_catalog(
    catalog: dict[str, Any],
    slos: list[dict[str, Any]],
    history_dir: Path,
    contracts_dir: Path,
) -> dict[str, dict[str, PillarFinding]]:
    """
    Evaluates every pillar of every cataloged table WITHOUT side effects (no alert, no incident,
    no report). Used by certification and by the maturity dashboard, so they read exactly the
    same verdicts as the checks runner. Tables without history get an empty dict.
    """
    findings_by_table: dict[str, dict[str, PillarFinding]] = {}
    for table_name in catalog["tables"]:
        history = load_history(table_name, history_dir)
        if not history:
            findings_by_table[table_name] = {}
            continue
        contract = load_contract(table_name, contracts_dir)
        findings_by_table[table_name], _ = evaluate_table(table_name, history, contract, slos)
    return findings_by_table


def run() -> tuple[dict[str, dict[str, PillarFinding]], list[SLOResult]]:
    """Executes all observability pillar checks, evaluates SLOs, dispatches alerts,
    logs incidents, and generates a Markdown summary report.
    """
    settings = load_settings()
    catalog = load_catalog(PROJECT_ROOT / settings["catalog_path"])
    slos = load_slos(PROJECT_ROOT / settings["slo_path"])

    history_dir = PROJECT_ROOT / settings["quality_history_path"]
    contracts_dir = PROJECT_ROOT / settings["contracts_path"]
    dq_reports_dir = PROJECT_ROOT / settings["dq_reports_path"]
    alerts_dir = PROJECT_ROOT / settings["alerts_path"]
    incidents_dir = PROJECT_ROOT / settings["incidents_path"]

    graph = build_lineage_graph(catalog)
    known_incidents = load_incidents(incidents_dir)
    findings_by_table: dict[str, dict[str, PillarFinding]] = {}

    for table_name, table_meta in catalog["tables"].items():
        history = load_history(table_name, history_dir)
        if not history:
            print(f"[checks_runner] '{table_name}': no history available yet, skipping pillars.")
            findings_by_table[table_name] = {}
            continue

        contract = load_contract(table_name, contracts_dir)
        findings, params_by_pillar = evaluate_table(table_name, history, contract, slos)
        findings_by_table[table_name] = findings
        print(f"[checks_runner] '{table_name}': " + ", ".join(f"{p}={f.status}" for p, f in findings.items()))

        snapshot_at = history[-1]["timestamp"]
        for pillar, finding in findings.items():
            if finding.status == "anomaly":
                handle_anomaly(
                    finding, table_meta, snapshot_at, params_by_pillar[pillar],
                    graph, known_incidents, alerts_dir, incidents_dir,
                )

    slo_results = evaluate_slos(slos, findings_by_table)

    n_slo_ok = sum(1 for r in slo_results if r.passed)
    print(f"\n[checks_runner] SLOs: {n_slo_ok}/{len(slo_results)} meeting objectives.")
    for r in slo_results:
        if not r.passed:
            print(f"  [FAILED] {r.slo_id}: {r.finding.detail}")

    generated_at = datetime.now(UTC).isoformat()
    dq_reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = dq_reports_dir / f"pillars_report_{generated_at.replace(':', '-')}.md"
    _write_report(findings_by_table, slo_results, generated_at, report_path)
    print(f"[checks_runner] report saved to: {report_path.relative_to(PROJECT_ROOT)}")

    return findings_by_table, slo_results


def _write_report(
    findings_by_table: dict[str, dict[str, PillarFinding]],
    slo_results: list[SLOResult],
    generated_at: str,
    path: Path,
) -> None:
    """Formats the observability pillars evaluation results and SLO outcomes into a Markdown report."""
    lines = ["# Observability Pillars Report", "", f"- Generated at: {generated_at}", ""]

    lines.append("## Service Level Objectives (SLOs)")
    lines.append("")
    lines.append("| SLO | Table | Pillar | Passed? | Finding |")
    lines.append("|---|---|---|---|---|")
    for r in slo_results:
        status = "✅" if r.passed else "❌"
        lines.append(f"| {r.slo_id} | {r.table} | {r.pillar} | {status} | {r.finding.detail} |")

    lines.append("")
    lines.append("## Pillars per Table")
    for table, findings in findings_by_table.items():
        lines.append(f"\n### {table}")
        if not findings:
            lines.append("No history available yet.")
            continue
        for pillar, finding in findings.items():
            flag = " ⚠️" if finding.status == "anomaly" else ""
            lines.append(f"- **{pillar}** [{finding.status}]{flag}: {finding.detail}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    """Runs the checks and returns a process exit code (0 = every SLO met, 1 = at least one breached)."""
    _, slo_results = run()
    return 0 if all(r.passed for r in slo_results) else 1


if __name__ == "__main__":
    sys.exit(main())
