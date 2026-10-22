"""
Pillar: Freshness / Up-to-dateness

Measures how long ago a table received its last successful ingestion and compares it
against an acceptable maximum delay threshold (SLO).

The clock is the timestamp of the last snapshot in reports/quality_history/, written only
when a table passes its contract. A table blocked by the circuit breaker therefore gets
"older" run after run until the freshness SLO fires - the block becomes visible downstream.
"""

from datetime import UTC, datetime
from typing import Any

from src.quality.pillars.base import PillarFinding


def check_freshness(
    table_name: str,
    history: list[dict[str, Any]],
    max_delay_hours: float,
    now: datetime | None = None,
) -> PillarFinding:
    """Evaluates whether the last successful ingestion meets the maximum delay SLO.

    Args:
        table_name: Name of the table being checked.
        history: Chronological list of historical snapshot records.
        max_delay_hours: Maximum acceptable delay in hours before triggering an anomaly.
        now: Reference "current time" (defaults to UTC now; injectable for tests).

    Returns:
        A PillarFinding object indicating whether data freshness is within acceptable limits.
    """
    if not history:
        return PillarFinding(table_name, "freshness", "insufficient_history", "No history available yet.")

    now = now or datetime.now(UTC)
    last_ts = datetime.fromisoformat(history[-1]["timestamp"])
    delay_hours = (now - last_ts).total_seconds() / 3600

    if delay_hours > max_delay_hours:
        return PillarFinding(
            table_name,
            "freshness",
            "anomaly",
            f"Last successful ingestion was {delay_hours:.1f}h ago (threshold: {max_delay_hours}h).",
        )

    return PillarFinding(table_name, "freshness", "ok", f"Updated {delay_hours:.2f}h ago.")
