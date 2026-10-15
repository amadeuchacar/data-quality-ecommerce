"""
Unit tests for the contract validator (src.ingestion.contract_validator).

Each test builds a small in-memory DataFrame and a minimal contract, breaks one rule
on purpose and asserts that the expected violation (and severity) is reported.
"""

from typing import Any

import pandas as pd
import pytest

from src.ingestion.contract_validator import validate_dataframe


@pytest.fixture
def contract() -> dict[str, Any]:
    return {
        "table": "orders",
        "primary_key": ["order_id", "item_id"],
        "columns": {
            "order_id": {"type": "string", "nullable": False},
            "item_id": {"type": "integer", "nullable": False},
            "status": {"type": "string", "nullable": False, "allowed_values": ["delivered", "canceled"]},
            "price": {"type": "float", "nullable": False, "min_value": 0, "max_value": 100},
            "created_at": {"type": "timestamp", "nullable": True},
        },
        "sla": {"min_expected_volume": 2},
    }


@pytest.fixture
def valid_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": ["a", "a", "b"],
            "item_id": [1, 2, 1],
            "status": ["delivered", "delivered", "canceled"],
            "price": [10.0, 99.9, 0.0],
            "created_at": ["2018-01-01 10:00:00", None, "2018-01-02 12:30:00"],
        }
    )


def _rules(result) -> list[str]:
    return [v.rule for v in result.violations]


def _violation(result, rule: str):
    return next(v for v in result.violations if v.rule == rule)


def test_valid_dataframe_passes(valid_df, contract):
    result = validate_dataframe(valid_df, contract)

    assert result.passed
    assert result.violations == []
    assert result.table == "orders"


def test_missing_column_is_blocking(valid_df, contract):
    result = validate_dataframe(valid_df.drop(columns="price"), contract)

    assert not result.passed
    violation = _violation(result, "missing_column")
    assert violation.column == "price"
    assert violation.severity == "blocking"


def test_unexpected_column_is_only_a_warning(valid_df, contract):
    result = validate_dataframe(valid_df.assign(new_col=1), contract)

    assert result.passed
    violation = _violation(result, "unexpected_column")
    assert violation.column == "new_col"
    assert violation.severity == "warning"


@pytest.mark.parametrize(
    ("column", "bad_value"),
    [
        ("item_id", "not-a-number"),
        ("item_id", 1.5),               # non-integral value declared as integer
        ("price", "ten"),
        ("created_at", "not-a-date"),
    ],
)
def test_type_mismatch_is_blocking(valid_df, contract, column, bad_value):
    df = valid_df.astype({column: "object"})
    df.loc[2, column] = bad_value   # last row: timestamp format is inferred from the first value

    result = validate_dataframe(df, contract)

    assert not result.passed
    violation = _violation(result, "type_mismatch")
    assert violation.column == column
    assert violation.detail.startswith("1 value(s)")


def test_nulls_in_non_nullable_column_are_blocking(valid_df, contract):
    valid_df.loc[0, "status"] = None

    result = validate_dataframe(valid_df, contract)

    assert not result.passed
    assert _violation(result, "unexpected_nulls").column == "status"


def test_nulls_in_nullable_column_are_accepted(valid_df, contract):
    valid_df["created_at"] = None

    result = validate_dataframe(valid_df, contract)

    assert result.passed


def test_uniqueness_violation(valid_df, contract):
    contract["columns"]["order_id"]["unique"] = True

    result = validate_dataframe(valid_df, contract)

    assert not result.passed
    violation = _violation(result, "uniqueness_violation")
    assert violation.column == "order_id"
    assert violation.detail.startswith("2 duplicate")   # both "a" rows are counted


def test_value_outside_allowed_list_is_blocking(valid_df, contract):
    valid_df.loc[1, "status"] = "lost"

    result = validate_dataframe(valid_df, contract)

    assert not result.passed
    assert "lost" in _violation(result, "unexpected_value").detail


def test_allowed_values_compare_against_cast_type(valid_df, contract):
    """Integer columns read as strings must still match integer allowed values."""
    contract["columns"]["item_id"]["allowed_values"] = [1, 2]
    valid_df["item_id"] = ["1", "2", "1"]

    result = validate_dataframe(valid_df, contract)

    assert result.passed


@pytest.mark.parametrize(("value", "rule"), [(-1.0, "below_min_value"), (100.01, "above_max_value")])
def test_business_ranges_are_blocking(valid_df, contract, value, rule):
    valid_df.loc[0, "price"] = value

    result = validate_dataframe(valid_df, contract)

    assert not result.passed
    assert _violation(result, rule).column == "price"


def test_range_limits_are_inclusive(valid_df, contract):
    valid_df["price"] = [0.0, 100.0, 50.0]

    assert validate_dataframe(valid_df, contract).passed


def test_duplicated_composite_primary_key_is_blocking(valid_df, contract):
    valid_df.loc[1, "item_id"] = 1   # ("a", 1) now appears twice

    result = validate_dataframe(valid_df, contract)

    assert not result.passed
    violation = _violation(result, "primary_key_violation")
    assert violation.column == "order_id, item_id"
    assert violation.detail.startswith("2 row(s)")


def test_repeated_values_in_part_of_composite_key_are_accepted(valid_df, contract):
    """order_id repeats on its own, but the (order_id, item_id) pair is unique."""
    assert valid_df["order_id"].duplicated().any()

    assert validate_dataframe(valid_df, contract).passed


def test_null_in_primary_key_is_blocking(valid_df, contract):
    contract["columns"]["item_id"]["nullable"] = True
    valid_df["item_id"] = valid_df["item_id"].astype("Int64")
    valid_df.loc[2, "item_id"] = pd.NA

    result = validate_dataframe(valid_df, contract)

    assert not result.passed
    assert "primary_key_nulls" in _rules(result)


def test_single_column_primary_key(valid_df, contract):
    contract["primary_key"] = "order_id"

    result = validate_dataframe(valid_df, contract)

    assert not result.passed
    assert _violation(result, "primary_key_violation").column == "order_id"


def test_primary_key_check_skipped_when_key_column_is_missing(valid_df, contract):
    result = validate_dataframe(valid_df.drop(columns="item_id"), contract)

    assert "missing_column" in _rules(result)
    assert "primary_key_violation" not in _rules(result)


def test_volume_below_sla_is_blocking(valid_df, contract):
    result = validate_dataframe(valid_df.head(1), contract)

    assert not result.passed
    violation = _violation(result, "volume_below_expected")
    assert violation.column is None


def test_as_dict_is_json_serializable(valid_df, contract):
    result = validate_dataframe(valid_df.drop(columns="price"), contract).as_dict()

    assert result["table"] == "orders"
    assert result["passed"] is False
    assert result["violations"][0] == {
        "rule": "missing_column",
        "column": "price",
        "detail": "Column 'price' expected by the contract is missing from the dataset.",
        "severity": "blocking",
    }
