"""
SLIs and SLOs

Loads the Service Level Objectives (SLOs) declared in config/slo.yaml and turns them into
pillar parameters. Every (table, pillar) pair is evaluated ONCE, with the parameters of its
SLO when one exists and the project defaults otherwise; the SLO result just reads the verdict
of that single evaluation. This guarantees that an alert, an incident and an SLO never
disagree about the same table.

- SLA: the agreement, written in each SLO's `description`
- SLI: the measured indicator, i.e. the pillar check (delay in hours, Z-score of the volume...)
- SLO: the target the SLI must meet (`max_delay_hours`, `z_threshold`...)
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from src.monitoring.anomaly_detection import DEFAULT_WINDOW
from src.quality.pillars.base import PillarFinding
from src.quality.pillars.distribution import check_distribution
from src.quality.pillars.freshness import check_freshness
from src.quality.pillars.schema import check_schema
from src.quality.pillars.volume import check_volume

PILLARS = ("freshness", "volume", "schema", "distribution")

# Parameters used for (table, pillar) pairs that have no SLO of their own
DEFAULT_PILLAR_PARAMS: dict[str, dict[str, Any]] = {
    "freshness": {"max_delay_hours": 24.0},
    "volume": {"z_threshold": 3.0, "method": "moving", "window": DEFAULT_WINDOW},
    "schema": {},
    "distribution": {"z_threshold": 3.0, "method": "moving", "window": DEFAULT_WINDOW},
}

# Keys of an SLO entry that are metadata, not pillar parameters
_SLO_METADATA_KEYS = {"id", "table", "pillar", "description"}


@dataclass
class SLOResult:
    """Represents the evaluation outcome for a specific Service Level Objective (SLO).

    Attributes:
        slo_id: Unique identifier of the SLO.
        table: Name of the table being evaluated.
        pillar: Observability pillar associated with the SLO.
        description: Human-readable description of the SLO rule.
        passed: True if the SLO check passed or had insufficient history, False if an anomaly was detected.
        finding: The underlying PillarFinding instance detailing the check results.
    """

    slo_id: str
    table: str
    pillar: str
    description: str
    passed: bool
    finding: PillarFinding


def load_slos(path: Path) -> list[dict[str, Any]]:
    """Loads the SLO definitions and rejects unknown pillars early.

    Args:
        path: Path to the slo.yaml file.

    Returns:
        A list of dictionary configurations representing each defined SLO.
    """
    with open(path, encoding="utf-8") as f:
        slos = yaml.safe_load(f)["slos"]

    for slo in slos:
        if slo["pillar"] not in PILLARS:
            raise ValueError(f"Unknown pillar in SLO '{slo['id']}': {slo['pillar']}")

    return slos


def pillar_params(table: str, pillar: str, slos: list[dict[str, Any]]) -> dict[str, Any]:
    """Resolves the parameters of a (table, pillar) pair: the defaults overridden by its SLO, if any."""
    params = dict(DEFAULT_PILLAR_PARAMS[pillar])
    for slo in slos:
        if slo["table"] == table and slo["pillar"] == pillar:
            params.update({k: v for k, v in slo.items() if k not in _SLO_METADATA_KEYS})
    return params


def evaluate_pillar(
    table: str,
    pillar: str,
    history: list[dict[str, Any]],
    contract: dict[str, Any] | None,
    params: dict[str, Any],
) -> PillarFinding:
    """Runs one observability pillar for one table with the given parameters.

    Args:
        table: Name of the table.
        pillar: One of PILLARS.
        history: Chronological snapshots of the table; the last one is the current execution.
        contract: The table's data contract (used for the volume fallback), if available.
        params: Pillar parameters, as resolved by pillar_params().

    Returns:
        The PillarFinding produced by the pillar.
    """
    if pillar == "freshness":
        return check_freshness(table, history, max_delay_hours=params["max_delay_hours"])

    if pillar == "volume":
        if not history:
            # Without ANY history there is no "current" row count to evaluate - do not fabricate
            # current_n_rows=0 (it would trigger a false anomaly against min_expected_volume).
            return PillarFinding(table, "volume", "insufficient_history", "No history available yet.")
        min_expected = (contract or {}).get("sla", {}).get("min_expected_volume")
        return check_volume(
            table,
            history[-1]["n_rows"],
            history,
            min_expected_volume=min_expected,
            z_threshold=params["z_threshold"],
            method=params["method"],
            window=params["window"],
        )

    if pillar == "schema":
        return check_schema(table, history)

    if pillar == "distribution":
        return check_distribution(
            table,
            history,
            z_threshold=params["z_threshold"],
            method=params["method"],
            window=params["window"],
        )

    raise ValueError(f"Unknown pillar: {pillar}")


def evaluate_slos(
    slos: list[dict[str, Any]],
    findings_by_table: dict[str, dict[str, PillarFinding]],
) -> list[SLOResult]:
    """Builds the SLO results from findings that were already computed with the SLO parameters.

    "insufficient_history" is not an SLO failure - it only means there is not enough evidence yet.
    """
    results = []
    for slo in slos:
        table, pillar = slo["table"], slo["pillar"]
        finding = findings_by_table.get(table, {}).get(pillar) or PillarFinding(
            table, pillar, "insufficient_history", "No history available yet."
        )
        results.append(
            SLOResult(
                slo_id=slo["id"],
                table=table,
                pillar=pillar,
                description=" ".join(slo["description"].split()),
                passed=finding.status != "anomaly",
                finding=finding,
            )
        )
    return results


def evaluate_all(
    history_by_table: dict[str, list[dict[str, Any]]],
    contracts_by_table: dict[str, dict[str, Any]],
    slos: list[dict[str, Any]],
) -> list[SLOResult]:
    """Evaluates only the SLO'd (table, pillar) pairs - convenience for callers without a runner.

    Args:
        history_by_table: Mapping of table names to their historical metrics snapshots.
        contracts_by_table: Mapping of table names to their data contracts.
        slos: SLO definitions, as returned by load_slos().

    Returns:
        A list of SLOResult objects for every evaluated SLO.
    """
    findings: dict[str, dict[str, PillarFinding]] = {}
    for slo in slos:
        table, pillar = slo["table"], slo["pillar"]
        findings.setdefault(table, {})[pillar] = evaluate_pillar(
            table,
            pillar,
            history_by_table.get(table, []),
            contracts_by_table.get(table),
            pillar_params(table, pillar, slos),
        )
    return evaluate_slos(slos, findings)
