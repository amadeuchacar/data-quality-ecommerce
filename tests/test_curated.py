"""
Unit tests for the curated layer (src.transform.build_curated) and the field-level lineage
registry (src.lineage.field_lineage), including the guard that keeps both in sync.
"""

import pandas as pd
import pytest

from src.lineage.field_lineage import (
    FIELD_LINEAGE_REGISTRY,
    derived_fields,
    describe_lineage,
    trace_to_source_fields,
)
from src.transform.build_curated import curate_order_items, curate_orders


@pytest.fixture
def order_items() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": ["a", "a", "b"],
            "order_item_id": [1, 2, 1],
            "price": [10.0, 20.0, 5.0],
            "freight_value": [1.0, 2.0, 0.5],
        }
    )


@pytest.fixture
def orders() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": ["a", "b", "c"],
            "order_delivered_customer_date": pd.to_datetime(["2018-01-12 15:00", "2018-01-08 09:00", None]),
            "order_estimated_delivery_date": pd.to_datetime(["2018-01-10", "2018-01-10", "2018-01-10"]),
        }
    )


# --- Transformations --------------------------------------------------------------------

def test_item_total_value_is_per_item(order_items):
    curated = curate_order_items(order_items)

    assert curated["item_total_value"].tolist() == [11.0, 22.0, 5.5]
    assert "item_total_value" not in order_items.columns      # input not mutated


def test_order_total_value_sums_the_items_of_each_order(orders, order_items):
    curated = curate_orders(orders, curate_order_items(order_items))

    totals = dict(zip(curated["order_id"], curated["order_total_value"], strict=True))
    assert totals["a"] == 33.0
    assert totals["b"] == 5.5
    assert pd.isna(totals["c"])          # no items: unknown, not zero


def test_delivery_delay_days_sign_and_nulls(orders, order_items):
    curated = curate_orders(orders, curate_order_items(order_items))

    assert curated["delivery_delay_days"].tolist()[:2] == [2, -2]     # late, early
    assert pd.isna(curated["delivery_delay_days"].iloc[2])            # not delivered yet
    assert str(curated["delivery_delay_days"].dtype) == "Int64"


def test_curate_orders_keeps_one_row_per_order(orders, order_items):
    assert len(curate_orders(orders, curate_order_items(order_items))) == len(orders)


# --- Lineage registry <-> code ----------------------------------------------------------

def test_every_curated_column_is_registered_in_field_lineage(orders, order_items):
    """A new derived column without a lineage entry must fail here, not drift silently."""
    items_curated = curate_order_items(order_items)
    orders_curated = curate_orders(orders, items_curated)

    assert set(items_curated.columns) - set(order_items.columns) == derived_fields("order_items_curated")
    assert set(orders_curated.columns) - set(orders.columns) == derived_fields("orders_curated")


@pytest.mark.parametrize("entry", FIELD_LINEAGE_REGISTRY, ids=lambda e: f"{e.target_table}.{e.target_field}")
def test_registry_sources_exist_in_their_tables(entry, orders, order_items):
    columns = {
        "order_items": set(order_items.columns),
        "orders": set(orders.columns),
        "order_items_curated": set(curate_order_items(order_items).columns),
    }

    assert set(entry.source_fields) <= columns[entry.source_table]


# --- Lineage traversal ------------------------------------------------------------------

def test_trace_resolves_multi_level_lineage_to_source_fields():
    assert trace_to_source_fields("orders_curated", "order_total_value") == [
        ("order_items", "price"),
        ("order_items", "freight_value"),
    ]


def test_source_field_traces_to_itself():
    assert trace_to_source_fields("orders", "order_id") == [("orders", "order_id")]


def test_describe_lineage():
    assert "source field" in describe_lineage("orders", "order_id")
    assert "order_items.price" in describe_lineage("orders_curated", "order_total_value")


def test_trace_raises_when_depth_is_exceeded():
    with pytest.raises(RecursionError):
        trace_to_source_fields("orders_curated", "order_total_value", _max_depth=0)
