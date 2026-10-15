"""
Unit tests for the contract type casting (src.ingestion.type_casting).
"""

import pandas as pd

from src.ingestion.type_casting import TYPE_CASTERS, cast_to_contract


def test_integer_caster_rejects_non_integral_values():
    cast = TYPE_CASTERS["integer"](pd.Series(["1", "2.0", "2.5", "x", None]))

    assert str(cast.dtype) == "Int64"
    assert cast.tolist()[:2] == [1, 2]
    assert cast.isna().tolist() == [False, False, True, True, True]


def test_timestamp_caster_turns_invalid_dates_into_nulls():
    cast = TYPE_CASTERS["timestamp"](pd.Series(["2018-01-01 10:00:00", "not-a-date"]))

    assert pd.api.types.is_datetime64_any_dtype(cast)
    assert cast.isna().tolist() == [False, True]


def test_cast_to_contract_applies_declared_types_only():
    df = pd.DataFrame(
        {
            "id": [1, 2],
            "qty": [3, 4],
            "created_at": ["2018-01-01", "2018-01-02"],
            "extra": ["kept", "as-is"],
        }
    )
    contract = {
        "columns": {
            "id": {"type": "string"},
            "qty": {"type": "integer"},
            "created_at": {"type": "timestamp"},
        }
    }

    typed = cast_to_contract(df, contract)

    assert str(typed["id"].dtype) == "string"
    assert str(typed["qty"].dtype) == "Int64"
    assert pd.api.types.is_datetime64_any_dtype(typed["created_at"])
    assert typed["extra"].dtype == df["extra"].dtype
    assert df["created_at"].dtype != typed["created_at"].dtype   # original is not mutated
