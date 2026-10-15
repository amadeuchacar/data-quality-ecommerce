"""
Stores a metric snapshot per table on every successful ingestion, building a historical
time series used by the Freshness, Volume, and Distribution observability pillars to detect
anomalies using Z-score deviations instead of rigid, hardcoded thresholds.

Each table has its own JSON Lines file in reports/quality_history/<table_name>.jsonl - containing
one record per successful pipeline execution.
"""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass
class TableSnapshot:
    """
    Represents a historical metrics snapshot for a dataset at a point in time.

    Attributes:
        table: name of the table
        timestamp: UTC ISO-8601 timestamp of when the snapshot was taken
        n_rows: total row count in the dataset
        columns: list of column names present
        dtypes: mapping of column names to their data types
        null_rates: mapping of column names to their missing-value percentages
        numeric_stats: statistical metrics (mean, standard deviation) for numeric columns
    """
    table: str
    timestamp: str
    n_rows: int
    columns: list[str]
    dtypes: dict[str, str]
    null_rates: dict[str, float]
    numeric_stats: dict[str, dict[str, float]]

    def as_dict(self) -> dict[str, Any]:
        """Serializes the snapshot dataclass into a standard dictionary"""
        return asdict(self)


def build_snapshot(table_name: str, df: pd.DataFrame) -> TableSnapshot:
    """
    Computes and builds a metrics snapshot from a pandas DataFrame.

    Calculates row counts, nullity rates, data types, and descriptive statistics
    for numerical columns to establish an observational baseline.

    Args:
        table_name: name of the table
        df: the pandas dataframe to profile
    
    Returns:
        A TableSnapshot containing the computed profiling metrics
    """
    null_rates = {col: round(100 * df[col].isna().mean(), 4) for col in df.columns}

    numeric_stats: dict[str, dict[str, float]] = {}
    for col in df.select_dtypes(include="number").columns:
        series = df[col].dropna()
        if len(series) == 0:
            continue

        numeric_stats[col] = {
            "mean": float(series.mean()),
            "std": float(series.std(ddof=0)) if len(series) > 1 else 0.0
        }

    return TableSnapshot(
        table=table_name,
        timestamp=datetime.now(UTC).isoformat(),
        n_rows=len(df),
        columns=list(df.columns),
        dtypes={col: str(dtype) for col, dtype in df.dtypes.items()},
        null_rates=null_rates,
        numeric_stats=numeric_stats
    )


def _history_path(table_name: str, history_dir: Path) -> Path:
    """Resolves the file path for a table's historical metrics JSONL file."""
    return history_dir / f"{table_name}.jsonl"


def append_snapshot(snapshot: TableSnapshot, history_dir: Path) -> None:
    """
    Appends a new metrics snapshot record to the table's JSONL history file.

    Args:
        snapshot: the TableSnapshot instance to record
        history_dir: directory where quality history files are stored
    """
    history_dir.mkdir(parents=True, exist_ok=True)
    path = _history_path(snapshot.table, history_dir)

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(snapshot.as_dict(), ensure_ascii=False) + "\n")


def load_history(table_name: str, history_dir: Path) -> list[dict[str, Any]]:
    """
    Loads all previously recorded historical snapshots for a given table in chronological order.

    Args:
        table_name: name of the target table
        history_dir: directory where quality history files are stored

    Returns:
        A list of snapshot dictionaries representing past execution metrics.
    """
    path = _history_path(table_name, history_dir)
    if not path.exists():
        return []

    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]