"""
Stage 4 — Maturity Dashboard

Consolidates into a single report the reliability KPIs of the data platform: incidents
(open, resolved, by severity), mean TTD, mean TTR, % of SLOs met and % of certified tables.

It reads the same pillar verdicts and certification results as the rest of the pipeline - when
the orchestrator already computed them, they are passed in and nothing is evaluated twice.

Usage:
    poetry run python -m src.reporting.maturity_dashboard
"""

import statistics
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from src.certification.certify import CertificationResult, evaluate_certification
from src.config import PROJECT_ROOT, load_catalog, load_settings
from src.incidents.incident_log import load_incidents, load_resolutions, open_incidents
from src.quality.checks_runner import evaluate_catalog
from src.quality.pillars.base import PillarFinding
from src.quality.sli_slo import SLOResult, evaluate_slos, load_slos


def _mean_or_none(values: list[float]) -> float | None:
    """Computes the arithmetic mean of a list of floats, or returns None if empty."""
    return round(statistics.mean(values), 2) if values else None


def _pct(part: int, total: int) -> float | None:
    return round(100 * part / total, 1) if total else None


def compute_kpis(
    incidents: list[dict[str, Any]],
    resolutions: list[dict[str, Any]],
    slo_results: list[SLOResult],
    certification_results: list[CertificationResult],
) -> dict[str, Any]:
    """Computes the maturity KPIs from already loaded records (no I/O)."""
    incident_ids = {inc["incident_id"] for inc in incidents}
    resolved = [r for r in resolutions if r["incident_id"] in incident_ids]
    n_resolved = len({r["incident_id"] for r in resolved})
    n_slo_passed = sum(1 for r in slo_results if r.passed)
    n_certified = sum(1 for r in certification_results if r.certified)

    return {
        "n_incidents": len(incidents),
        "n_open": len(open_incidents(incidents, resolutions)),
        "n_resolved": n_resolved,
        "pct_resolved": _pct(n_resolved, len(incidents)),
        "incidents_by_severity": dict(Counter(inc.get("severity", "unknown") for inc in incidents)),
        "ttd_mean_seconds": _mean_or_none([inc["ttd_seconds"] for inc in incidents]),
        "ttr_mean_seconds": _mean_or_none([r["ttr_seconds"] for r in resolved]),
        "n_slo_total": len(slo_results),
        "n_slo_passed": n_slo_passed,
        "pct_slo_ok": _pct(n_slo_passed, len(slo_results)),
        "n_tables": len(certification_results),
        "n_certified": n_certified,
        "pct_certified": _pct(n_certified, len(certification_results)),
    }


def build_dashboard(
    findings_by_table: dict[str, dict[str, PillarFinding]] | None = None,
    certification_results: list[CertificationResult] | None = None,
) -> dict[str, Any]:
    """Gathers the inputs (reusing the ones passed in) and returns KPIs plus the detail rows."""
    settings = load_settings()
    catalog = load_catalog(PROJECT_ROOT / settings["catalog_path"])
    slos = load_slos(PROJECT_ROOT / settings["slo_path"])
    incidents_dir = PROJECT_ROOT / settings["incidents_path"]

    if findings_by_table is None:
        findings_by_table = evaluate_catalog(
            catalog, slos, PROJECT_ROOT / settings["quality_history_path"], PROJECT_ROOT / settings["contracts_path"]
        )
    if certification_results is None:
        certification_results = evaluate_certification(settings, catalog, findings_by_table)

    slo_results = evaluate_slos(slos, findings_by_table)
    kpis = compute_kpis(
        load_incidents(incidents_dir), load_resolutions(incidents_dir), slo_results, certification_results
    )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        **kpis,
        "slo_results": slo_results,
        "certification_results": certification_results,
    }


def _fmt_duration(seconds: float | None) -> str:
    """Human-readable duration: seconds below 1 minute, minutes below 1 hour, hours above."""
    if seconds is None:
        return "N/A (no data yet)"
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}min"
    return f"{seconds / 3600:.1f}h"


def render_dashboard_markdown(dashboard: dict[str, Any]) -> str:
    """Formats the data maturity dashboard metrics and tables into a clean Markdown report."""
    def fmt(value: Any, suffix: str = "") -> str:
        return f"{value}{suffix}" if value is not None else "N/A (no data yet)"

    by_severity = ", ".join(f"{k}: {v}" for k, v in sorted(dashboard["incidents_by_severity"].items())) or "-"

    lines = [
        "# Data Quality Maturity Dashboard",
        "",
        f"Generated at: {dashboard['generated_at']}",
        "",
        "## Reliability KPIs",
        "",
        "| KPI | Value |",
        "|---|---|",
        f"| Recorded incidents | {dashboard['n_incidents']} ({by_severity}) |",
        f"| Open incidents | {dashboard['n_open']} |",
        f"| Resolved incidents | {dashboard['n_resolved']} ({fmt(dashboard['pct_resolved'], '%')}) |",
        f"| Mean TTD (time to detection) | {_fmt_duration(dashboard['ttd_mean_seconds'])} |",
        f"| Mean TTR (time to resolution) | {_fmt_duration(dashboard['ttr_mean_seconds'])} |",
        (
            f"| SLOs meeting objective | {dashboard['n_slo_passed']}/{dashboard['n_slo_total']} "
            f"({fmt(dashboard['pct_slo_ok'], '%')}) |"
        ),
        (
            f"| Certified tables | {dashboard['n_certified']}/{dashboard['n_tables']} "
            f"({fmt(dashboard['pct_certified'], '%')}) |"
        ),
        "",
        "## SLO Details",
        "",
        "| SLO | Table | Pillar | Status |",
        "|---|---|---|---|",
    ]
    for r in dashboard["slo_results"]:
        status = "✅" if r.passed else "❌"
        lines.append(f"| {r.slo_id} | {r.table} | {r.pillar} | {status} |")

    lines += ["", "## Certification Details", "", "| Table | Certified? | Reasons (if not) |", "|---|---|---|"]
    for r in dashboard["certification_results"]:
        status = "✅" if r.certified else "❌"
        reasons = "; ".join(r.reasons) if r.reasons else "-"
        lines.append(f"| {r.table} | {status} | {reasons} |")

    return "\n".join(lines) + "\n"


def run(
    findings_by_table: dict[str, dict[str, PillarFinding]] | None = None,
    certification_results: list[CertificationResult] | None = None,
) -> dict[str, Any]:
    """Generates, renders, and persists the data maturity dashboard report."""
    dashboard = build_dashboard(findings_by_table, certification_results)

    out_dir = PROJECT_ROOT / load_settings()["dashboard_path"]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"dashboard_{dashboard['generated_at'].replace(':', '-')}.md"
    out_path.write_text(render_dashboard_markdown(dashboard), encoding="utf-8")

    # console gets a plain-text summary (the Markdown has emojis that legacy consoles cannot print)
    print(
        f"[maturity_dashboard] incidents: {dashboard['n_incidents']} ({dashboard['n_open']} open) | "
        f"mean TTD: {_fmt_duration(dashboard['ttd_mean_seconds'])} | "
        f"mean TTR: {_fmt_duration(dashboard['ttr_mean_seconds'])}"
    )
    print(
        f"[maturity_dashboard] SLOs met: {dashboard['n_slo_passed']}/{dashboard['n_slo_total']} | "
        f"certified tables: {dashboard['n_certified']}/{dashboard['n_tables']}"
    )
    print(f"[maturity_dashboard] saved to: {out_path.relative_to(PROJECT_ROOT)}")

    return dashboard


if __name__ == "__main__":
    run()
