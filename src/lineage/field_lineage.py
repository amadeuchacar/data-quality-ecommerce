"""
Stage 4 — Field-Level Lineage

Since this project uses pandas instead of SQL, there is no query to parse (the approach
described in Chapter 7 as most common). Instead, each field transformation is EXPLICITLY
DECLARED in this registry:

    Source Table -> Source Fields -> Transformation -> Target Field -> Target Table

Every column created in src/transform/build_curated.py has a corresponding entry here, and
tests/test_curated.py fails if a new curated column is added without one - the registry
cannot silently drift away from the code it documents.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldTransformation:
    """Represents a declared field-level lineage transformation.

    Attributes:
        source_table: Name of the upstream source table.
        source_fields: Upstream source field names.
        transformation: Human-readable expression or description of the transformation.
        target_table: Name of the downstream target table.
        target_field: Name of the resulting target field.
    """

    source_table: str
    source_fields: tuple[str, ...]
    transformation: str
    target_table: str
    target_field: str


FIELD_LINEAGE_REGISTRY: list[FieldTransformation] = [
    FieldTransformation(
        source_table="order_items",
        source_fields=("price", "freight_value"),
        transformation="item_total_value = price + freight_value (one row per item, NOT per order)",
        target_table="order_items_curated",
        target_field="item_total_value",
    ),
    FieldTransformation(
        source_table="order_items_curated",
        source_fields=("item_total_value",),
        transformation="order_total_value = SUM(item_total_value) GROUP BY order_id",
        target_table="orders_curated",
        target_field="order_total_value",
    ),
    FieldTransformation(
        source_table="orders",
        source_fields=("order_delivered_customer_date", "order_estimated_delivery_date"),
        transformation=(
            "delivery_delay_days = order_delivered_customer_date - order_estimated_delivery_date "
            "(in days; positive = delayed, negative = delivered ahead of schedule)"
        ),
        target_table="orders_curated",
        target_field="delivery_delay_days",
    ),
]


def lineage_for_target_field(target_table: str, target_field: str) -> FieldTransformation | None:
    """Retrieves the registered field transformation entry for a given target table and field."""
    for entry in FIELD_LINEAGE_REGISTRY:
        if entry.target_table == target_table and entry.target_field == target_field:
            return entry
    return None


def derived_fields(target_table: str) -> set[str]:
    """Every field of `target_table` produced by a registered transformation."""
    return {e.target_field for e in FIELD_LINEAGE_REGISTRY if e.target_table == target_table}


def trace_to_source_fields(
    target_table: str, target_field: str, _depth: int = 0, _max_depth: int = 10
) -> list[tuple[str, str]]:
    """Recursively resolves field dependencies back to base source fields (Chapter 7:

    "lineage should not merely identify the direct relationship... but reconstruct
    the entire dependency chain"). Duplicates are removed, keeping first-seen order.
    """
    if _depth > _max_depth:
        raise RecursionError(f"Maximum lineage depth exceeded for {target_table}.{target_field}")

    entry = lineage_for_target_field(target_table, target_field)
    if entry is None:
        return [(target_table, target_field)]

    resolved: list[tuple[str, str]] = []
    for source_field in entry.source_fields:
        for source in trace_to_source_fields(entry.source_table, source_field, _depth + 1, _max_depth):
            if source not in resolved:
                resolved.append(source)
    return resolved


def describe_lineage(target_table: str, target_field: str) -> str:
    """Generates a human-readable description of a field's transformation and upstream source lineage."""
    entry = lineage_for_target_field(target_table, target_field)
    if entry is None:
        return f"'{target_table}.{target_field}' is a source field (no registered transformation)."
    sources = trace_to_source_fields(target_table, target_field)
    sources_str = ", ".join(f"{t}.{f}" for t, f in sources)
    return f"'{target_table}.{target_field}' = {entry.transformation} (sources: {sources_str})"
