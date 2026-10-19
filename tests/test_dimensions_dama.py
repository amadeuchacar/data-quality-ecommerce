"""
Unit tests for the DAMA UK quality dimensions (src.quality.dimensions_dama).

Parent tables for the Consistency dimension come from an in-memory lookup, so no
staging file is needed.
"""

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from src.quality.dimensions_dama import (
    StagingReferenceValues,
    completeness,
    consistency,
    score_table,
    uniqueness,
    validity,
)


@pytest.fixture
def contract() -> dict[str, Any]:
    return {
        "table": "order_items",
        "columns": {
            "order_id": {"type": "string", "nullable": False},
            "item_id": {"type": "integer", "nullable": False},
            "status": {"type": "string", "nullable": False, "allowed_values": ["delivered", "canceled"]},
            "price": {"type": "float", "nullable": False, "min_value": 0, "max_value": 100},
            "comment": {"type": "string", "nullable": True},
        },
    }


@pytest.fixture
def df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": ["a", "a", "b", "c"],
            "item_id": [1, 2, 1, 1],
            "status": ["delivered", "delivered", "canceled", "delivered"],
            "price": [10.0, 20.0, 30.0, 40.0],
            "comment": ["ok", None, None, None],
        }
    )


def _lookup(parents: dict[tuple[str, str], list[Any]]):
    """Builds an in-memory reference lookup: (table, column) -> values, or None if absent."""
    def lookup(table_name: str, column: str) -> pd.Index | None:
        values = parents.get((table_name, column))
        return None if values is None else pd.Index(values)
    return lookup


# --- Completeness ---------------------------------------------------------------------

def test_completeness_per_column(df):
    scores = completeness(df)

    assert scores["order_id"] == 100.0
    assert scores["comment"] == 25.0


def test_completeness_of_empty_table_is_not_applicable(df):
    assert set(completeness(df.head(0)).values()) == {None}


# --- Uniqueness -----------------------------------------------------------------------

def test_uniqueness_with_composite_key(df):
    assert uniqueness(df, ["order_id", "item_id"]) == 100.0


def test_uniqueness_counts_only_the_repeated_rows(df):
    # "a" appears twice: 1 of the 4 rows is a repeat of the key
    assert uniqueness(df, ["order_id"]) == 75.0


@pytest.mark.parametrize("key_columns", [[], ["not_a_column"]])
def test_uniqueness_without_usable_key_is_not_applicable(df, key_columns):
    assert uniqueness(df, key_columns) is None


# --- Validity -------------------------------------------------------------------------

def test_valid_dataframe_scores_100(df, contract):
    assert set(validity(df, contract).values()) == {100.0}


def test_validity_is_gradual(df, contract):
    df.loc[0, "status"] = "lost"        # outside allowed values
    df.loc[1, "price"] = -5.0           # below min_value
    df.loc[2, "price"] = 150.0          # above max_value

    scores = validity(df, contract)

    assert scores["status"] == 75.0
    assert scores["price"] == 50.0


def test_validity_counts_type_mismatch(df, contract):
    df = df.astype({"item_id": "object"})
    df.loc[3, "item_id"] = "x"

    assert validity(df, contract)["item_id"] == 75.0


def test_nulls_are_not_invalid(df, contract):
    """Missing values belong to completeness, not validity."""
    df["price"] = pd.array([10.0, None, None, 40.0], dtype="Float64")

    assert validity(df, contract)["price"] == 100.0


def test_validity_skips_columns_missing_from_the_data(df, contract):
    assert "price" not in validity(df.drop(columns="price"), contract)


# --- Consistency ----------------------------------------------------------------------

FK = [{"column": "order_id", "references": "orders.order_id"}]


def test_consistency_with_all_parents_present(df):
    lookup = _lookup({("orders", "order_id"): ["a", "b", "c"]})

    assert consistency(df, FK, lookup) == {"order_id -> orders.order_id": 100.0}


def test_consistency_measures_orphans(df):
    lookup = _lookup({("orders", "order_id"): ["a", "b"]})   # "c" is an orphan (1 of 4 rows)

    assert consistency(df, FK, lookup)["order_id -> orders.order_id"] == 75.0


def test_null_foreign_keys_are_not_orphans(df):
    df.loc[3, "order_id"] = None
    lookup = _lookup({("orders", "order_id"): ["a", "b"]})

    assert consistency(df, FK, lookup)["order_id -> orders.order_id"] == 100.0


def test_consistency_not_applicable_when_parent_is_missing(df):
    assert consistency(df, FK, _lookup({}))["order_id -> orders.order_id"] is None


def test_consistency_not_applicable_when_fk_column_is_missing(df):
    lookup = _lookup({("orders", "order_id"): ["a"]})

    assert consistency(df.drop(columns="order_id"), FK, lookup)["order_id -> orders.order_id"] is None


def test_staging_reference_values_reads_and_caches(tmp_path: Path):
    pd.DataFrame({"order_id": ["a", "b", None], "other": [1, 2, 3]}).to_parquet(tmp_path / "orders.parquet")
    lookup = StagingReferenceValues(tmp_path)

    first = lookup("orders", "order_id")

    assert sorted(first) == ["a", "b"]
    assert lookup("orders", "order_id") is first             # served from cache
    assert lookup("orders", "missing_column") is None
    assert lookup("missing_table", "order_id") is None


# --- Table scorecard ------------------------------------------------------------------

def test_score_table_summary_uses_worst_score(df, contract):
    df.loc[0, "price"] = -1.0
    table_meta = {"primary_key": ["order_id", "item_id"], "foreign_keys": FK}
    lookup = _lookup({("orders", "order_id"): ["a", "b"]})

    result = score_table(df, contract, table_meta, lookup)

    assert result["n_rows"] == 4
    assert result["optional_columns"] == ["comment"]
    assert result["summary"] == {
        "completeness_pct": 100.0,      # the optional "comment" (25%) does not count
        "uniqueness_pct": 100.0,
        "validity_pct": 75.0,           # worst column: price
        "consistency_pct": 75.0,
    }


def test_score_table_without_keys(df, contract):
    result = score_table(df, contract, {"primary_key": None, "foreign_keys": []}, _lookup({}))

    assert result["summary"]["uniqueness_pct"] is None
    assert result["summary"]["consistency_pct"] is None
    assert result["consistency"] == {}
