"""Shared fixtures and builders for the observability tests."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def stable_history(n: int = 5, mean: float = 50.0, n_rows: int = 100_000) -> list[dict[str, Any]]:
    """A stable series of daily snapshots, the last one taken at NOW."""
    return [
        {
            "table": "orders",
            "timestamp": (NOW - timedelta(days=n - 1 - i)).isoformat(),
            "n_rows": n_rows,
            "columns": ["a", "b"],
            "dtypes": {"a": "object", "b": "float64"},
            "null_rates": {"a": 0.0, "b": 1.0},
            "numeric_stats": {"b": {"mean": mean + (i * 0.01), "std": 2.0}},
        }
        for i in range(n)
    ]


@pytest.fixture
def history() -> list[dict[str, Any]]:
    return stable_history()
