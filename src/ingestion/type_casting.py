"""
Type casting rules shared by the contract validator and the staging writer.

Each caster receives a raw Series (as read from the CSV) and returns a Series converted
to the contract type. Values that cannot be safely converted become null, so the
validator can count them by comparing nulls before and after the cast.
"""

from collections.abc import Callable
from typing import Any

import pandas as pd


def _to_integer(series: pd.Series) -> pd.Series:
    """Casts to nullable Int64. Non-integral numbers (e.g. 1.5) count as failed casts."""
    numeric = pd.to_numeric(series, errors="coerce")
    return numeric.where(numeric % 1 == 0).astype("Int64")


TYPE_CASTERS: dict[str, Callable[[pd.Series], pd.Series]] = {
    "string": lambda s: s.astype("string"),
    "integer": _to_integer,
    "float": lambda s: pd.to_numeric(s, errors="coerce").astype("float64"),
    "timestamp": lambda s: pd.to_datetime(s, errors="coerce"),
}


def cast_to_contract(df: pd.DataFrame, contract: dict[str, Any]) -> pd.DataFrame:
    """
    Returns a copy of the DataFrame with each column cast to the type declared in the
    contract. Columns without a declared (or known) type are kept as read.

    Args:
        df: the raw pandas dataframe
        contract: the contract specification dictionary

    Returns:
        A new DataFrame with the contract types applied.
    """
    typed = df.copy()
    for col, rules in contract.get("columns", {}).items():
        caster = TYPE_CASTERS.get(rules.get("type"))
        if col in typed.columns and caster is not None:
            typed[col] = caster(typed[col])

    return typed
