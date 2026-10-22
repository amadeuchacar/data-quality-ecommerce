"""
Pillar: Schema

Compares columns and data types from the current execution run against the immediate
previous execution run of the same table to detect schema drift.
"""

from typing import Any

from src.quality.pillars.base import PillarFinding


def check_schema(table_name: str, history: list[dict[str, Any]]) -> PillarFinding:
    """Evaluates schema stability by comparing current columns and types against the previous run.

    Args:
        table_name: Name of the table being checked.
        history: Chronological list of historical snapshot records.

    Returns:
        A PillarFinding object indicating whether schema changes or drift were detected.
    """
    if len(history) < 2:
        return PillarFinding(
            table_name, "schema", "insufficient_history", "No previous execution found for comparison."
        )

    current = history[-1]
    previous = history[-2]

    current_cols = set(current["columns"])
    previous_cols = set(previous["columns"])
    added = current_cols - previous_cols
    removed = previous_cols - current_cols
    changed_types = {
        col: (previous["dtypes"].get(col), current["dtypes"].get(col))
        for col in current_cols & previous_cols
        if previous["dtypes"].get(col) != current["dtypes"].get(col)
    }

    if added or removed or changed_types:
        details = []
        if added:
            details.append(f"added columns: {sorted(added)}")
        if removed:
            details.append(f"removed columns: {sorted(removed)}")
        if changed_types:
            details.append(f"changed data types: {changed_types}")
        return PillarFinding(table_name, "schema", "anomaly", "; ".join(details))

    return PillarFinding(table_name, "schema", "ok", "Schema is stable compared to the previous execution.")