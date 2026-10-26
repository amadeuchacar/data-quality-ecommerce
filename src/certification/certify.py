"""
Stage 4 — Data Certification

Simplified implementation of the 7 steps for data certification:
    1. Develop observability         -> Stage 3 (the pillars, src/quality/pillars/)
    2. Assign owners                 -> catalog/inventory.yaml (owner / owner_default)
    3. Define "good data"            -> contracts/*.contract.yaml (Stage 1)
    4. Define SLAs, SLOs, SLIs       -> config/slo.yaml (Stage 3)
    5. Create incident process       -> src/incidents/ (log, resolution, RCA, postmortem)
    6. Certification mechanism       -> THIS MODULE
    7. Train producers/consumers     -> README.md (not automatable - depends on people, not code)

A table is certified when, in the most recent evaluation:
- it has a defined owner (specific or inherited from owner_default);
- there is enough history for EVERY pillar to give a verdict (no "insufficient_history":
  absence of evidence is not evidence of quality);
- none of the pillars (freshness/volume/schema/distribution) is in an "anomaly" state;
- it has no open incident (the table is certified again only after the resolution is registered).

The pillar verdicts are the same ones the checks runner uses (same SLO parameters), so a table
can never be certified while its SLO is breached.

The result is written to reports/certification/certification_status.yaml: generated state stays
out of the versioned catalog/, which is maintained by humans.

Usage:
    poetry run python -m src.certification.certify
"""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from src.config import PROJECT_ROOT, load_catalog, load_settings
from src.incidents.incident_log import load_incidents, load_resolutions, open_incidents
from src.quality.checks_runner import evaluate_catalog
from src.quality.pillars.base import PillarFinding
from src.quality.sli_slo import load_slos


@dataclass
class CertificationResult:
    """Represents the data certification evaluation outcome for a specific table.

    Attributes:
        table: Name of the certified or evaluated table.
        certified: True if all certification criteria are met, False otherwise.
        owner: The assigned owner email or identifier.
        reasons: List of blocking reasons preventing certification (empty if certified).
    """

    table: str
    certified: bool
    owner: str | None
    reasons: list[str]

    def as_dict(self) -> dict[str, Any]:
        """Serializes the CertificationResult dataclass into a standard dictionary."""
        return asdict(self)


def certify_table(
    table_name: str,
    table_meta: dict[str, Any],
    owner_default: str | None,
    findings: dict[str, PillarFinding],
    table_open_incidents: list[dict[str, Any]],
) -> CertificationResult:
    """Evaluates whether a table meets every certification criterion.

    Args:
        table_name: Name of the table being evaluated.
        table_meta: Metadata dictionary for the table from the catalog inventory.
        owner_default: Default fallback owner defined at the catalog level.
        findings: Pillar findings of the table (empty when there is no history).
        table_open_incidents: Unresolved incidents of this table.

    Returns:
        A CertificationResult instance containing certification status and any blocking reasons.
    """
    reasons: list[str] = []

    owner = table_meta.get("owner") or owner_default
    if not owner:
        reasons.append("No owner defined (neither specific nor owner_default).")

    if not findings:
        reasons.append("No ingestion history available yet - unable to evaluate observability pillars.")

    for pillar_name, finding in findings.items():
        if finding.status == "insufficient_history":
            reasons.append(f"Pillar '{pillar_name}' has insufficient history: {finding.detail}")
        elif finding.status == "anomaly":
            reasons.append(f"Pillar '{pillar_name}' in anomaly state: {finding.detail}")

    for incident in table_open_incidents:
        reasons.append(f"Open incident {incident['incident_id']} ({incident['pillar']}) - register its resolution.")

    return CertificationResult(table=table_name, certified=not reasons, owner=owner, reasons=reasons)


def certify_all(
    catalog: dict[str, Any],
    findings_by_table: dict[str, dict[str, PillarFinding]],
    unresolved: list[dict[str, Any]],
) -> list[CertificationResult]:
    """Certifies every cataloged table from already computed findings and open incidents (no I/O)."""
    owner_default = catalog.get("owner_default")
    return [
        certify_table(
            table_name,
            table_meta,
            owner_default,
            findings_by_table.get(table_name, {}),
            [inc for inc in unresolved if inc["table"] == table_name],
        )
        for table_name, table_meta in catalog["tables"].items()
    ]


def evaluate_certification(
    settings: dict[str, Any],
    catalog: dict[str, Any],
    findings_by_table: dict[str, dict[str, PillarFinding]] | None = None,
) -> list[CertificationResult]:
    """Loads what certification depends on (findings, open incidents) and certifies every table."""
    if findings_by_table is None:
        findings_by_table = evaluate_catalog(
            catalog,
            load_slos(PROJECT_ROOT / settings["slo_path"]),
            PROJECT_ROOT / settings["quality_history_path"],
            PROJECT_ROOT / settings["contracts_path"],
        )
    incidents_dir = PROJECT_ROOT / settings["incidents_path"]
    unresolved = open_incidents(load_incidents(incidents_dir), load_resolutions(incidents_dir))
    return certify_all(catalog, findings_by_table, unresolved)


def write_status(results: list[CertificationResult], output_path: Path) -> None:
    """Persists the latest certification status of every table."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "tables": {r.table: r.as_dict() for r in results},
    }
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)


def run(findings_by_table: dict[str, dict[str, PillarFinding]] | None = None) -> list[CertificationResult]:
    """Certifies every table and writes the status file.

    Args:
        findings_by_table: Findings already computed by the checks runner in the same execution
            (the orchestrator passes them to avoid re-evaluating); computed here when omitted.
    """
    settings = load_settings()
    catalog = load_catalog(PROJECT_ROOT / settings["catalog_path"])
    results = evaluate_certification(settings, catalog, findings_by_table)

    for result in results:
        flag = "CERTIFIED" if result.certified else "not certified"
        print(f"[certify] {result.table}: {flag}")
        for reason in result.reasons:
            print(f"    - {reason}")

    output_path = PROJECT_ROOT / settings["certification_path"] / "certification_status.yaml"
    write_status(results, output_path)

    n_certified = sum(1 for r in results if r.certified)
    print(f"\n[certify] {n_certified}/{len(results)} tables certified.")
    print(f"[certify] status saved to: {output_path.relative_to(PROJECT_ROOT)}")

    return results


if __name__ == "__main__":
    run()
