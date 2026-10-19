"""
DAMA UK Data Quality Dimensions - staging scorecard

Complements contract_validator.py (which answers "pass or fail?") with gradual quality
metrics, in percent, designed for human readability in a scorecard format. Covers 4 of the
6 DAMA UK dimensions (Timeliness and Accuracy are out of scope for now):

- Completeness -> % of non-null values per column
- Uniqueness   -> % of rows not duplicated on the primary key (catalog)
- Validity     -> % of values respecting types, allowed values and ranges (contract)
- Consistency  -> % of foreign key values found in the referenced parent table (catalog)

Runs on the staging layer, after src.ingestion.load_raw. Only tables that passed their
contract reach staging, so Validity and Uniqueness work here as a certification of the gate
(expected 100%). The gradual signal comes from the Completeness of optional columns and from
Consistency - a cross-table check that the per-table contract cannot make.

Usage:
    poetry run python -m src.quality.dimensions_dama
"""

import json
import operator
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from functools import reduce
from pathlib import Path
from typing import Any

import pandas as pd

from src.config import PROJECT_ROOT, load_catalog, load_settings
from src.ingestion.contract_validator import column_rule_violations, load_contract

# (table, column) -> distinct values of that column in staging, or None if unavailable
ReferenceLookup = Callable[[str, str], pd.Index | None]


class StagingReferenceValues:
    """
    Reads (lazily, once) the distinct values of a parent table column from staging.

    Only the referenced column is read from the Parquet file, and the result is cached:
    a parent shared by several foreign keys (e.g. orders.order_id) is scanned a single time.
    """

    def __init__(self, staging_dir: Path) -> None:
        self._staging_dir = staging_dir
        self._cache: dict[tuple[str, str], pd.Index | None] = {}

    def __call__(self, table_name: str, column: str) -> pd.Index | None:
        key = (table_name, column)
        if key not in self._cache:
            path = self._staging_dir / f"{table_name}.parquet"
            try:
                values = pd.read_parquet(path, columns=[column])[column]
                self._cache[key] = pd.Index(values.dropna().unique())
            except (OSError, ValueError):   # parent not in staging, or column not found
                self._cache[key] = None
        return self._cache[key]


def _pct(ratio: float) -> float:
    """Converts a 0-1 ratio into a percentage rounded to 2 decimal places."""
    return round(100 * float(ratio), 2)


def _worst(values: Iterable[float | None]) -> float | None:
    """Returns the lowest score, ignoring dimensions that do not apply (None)."""
    scores = [v for v in values if v is not None]
    return min(scores) if scores else None


def _key_columns(primary_key: str | list[str] | None) -> list[str]:
    """Normalizes a catalog primary key (single, composite or absent) into a list."""
    if primary_key is None:
        return []
    return [primary_key] if isinstance(primary_key, str) else list(primary_key)


def completeness(df: pd.DataFrame) -> dict[str, float | None]:
    """Calculates the percentage of non-null values for each column."""
    if df.empty:
        return {col: None for col in df.columns}
    return {col: _pct(df[col].notna().mean()) for col in df.columns}


def uniqueness(df: pd.DataFrame, key_columns: list[str]) -> float | None:
    """Calculates the percentage of rows that are not a repeat of the (composite) key."""
    subset = [c for c in key_columns if c in df.columns]
    if not subset or df.empty:
        return None
    return _pct(1 - df.duplicated(subset=subset).mean())


def validity(df: pd.DataFrame, contract: dict[str, Any]) -> dict[str, float]:
    """
    Calculates the percentage of valid values per column, using the same rules as the
    contract validator (type, accepted values and business ranges). Nulls count as valid:
    missing values are measured by completeness, not validity.
    """
    scores: dict[str, float] = {}
    if df.empty:
        return scores

    for col, rules in contract.get("columns", {}).items():
        if col not in df.columns:
            continue

        masks = column_rule_violations(df[col], rules)
        if not masks:
            scores[col] = 100.0
            continue

        invalid = reduce(operator.or_, masks.values())
        scores[col] = _pct(1 - invalid.mean())

    return scores


def consistency(
    df: pd.DataFrame,
    foreign_keys: list[dict[str, str]],
    reference_values: ReferenceLookup,
) -> dict[str, float | None]:
    """
    Measures referential integrity: the percentage of non-null foreign key values that
    exist in the referenced parent column. None = relation could not be evaluated
    (column missing, parent not in staging, or no values to check).
    """
    results: dict[str, float | None] = {}

    for fk in foreign_keys:
        col = fk["column"]
        ref_table, ref_col = fk["references"].split(".", 1)
        label = f"{col} -> {ref_table}.{ref_col}"

        if col not in df.columns:
            results[label] = None
            continue

        non_null = df[col].dropna()
        parent_values = reference_values(ref_table, ref_col)
        if parent_values is None or non_null.empty:
            results[label] = None
            continue

        results[label] = _pct(non_null.isin(parent_values).mean())

    return results


def score_table(
    df: pd.DataFrame,
    contract: dict[str, Any],
    table_meta: dict[str, Any],
    reference_values: ReferenceLookup,
) -> dict[str, Any]:
    """
    Computes the four dimensions for one table, plus a summary with the worst score of each.

    Completeness is summarized over required (non-nullable) columns only: an optional
    column with nulls is expected behavior, not a quality gap.
    """
    columns = contract.get("columns", {})
    optional_columns = sorted(col for col, rules in columns.items() if rules.get("nullable", True))
    required_columns = [col for col in columns if col not in optional_columns]

    completeness_scores = completeness(df)
    uniqueness_score = uniqueness(df, _key_columns(table_meta.get("primary_key")))
    validity_scores = validity(df, contract)
    consistency_scores = consistency(df, table_meta.get("foreign_keys") or [], reference_values)

    return {
        "n_rows": len(df),
        "summary": {
            "completeness_pct": _worst(completeness_scores.get(c) for c in required_columns),
            "uniqueness_pct": uniqueness_score,
            "validity_pct": _worst(validity_scores.values()),
            "consistency_pct": _worst(consistency_scores.values()),
        },
        "completeness": completeness_scores,
        "optional_columns": optional_columns,
        "validity": validity_scores,
        "consistency": consistency_scores,
    }


def run() -> dict[str, Any]:
    """Executes the DAMA UK quality dimensions scorecard across all cataloged tables."""
    settings = load_settings()
    catalog = load_catalog(PROJECT_ROOT / settings["catalog_path"])
    contracts_dir = PROJECT_ROOT / settings["contracts_path"]
    staging_dir = PROJECT_ROOT / settings["staging_path"]
    dq_reports_dir = PROJECT_ROOT / settings["dq_reports_path"]
    dq_reports_dir.mkdir(parents=True, exist_ok=True)

    reference_values = StagingReferenceValues(staging_dir)
    generated_at = datetime.now(UTC).isoformat()
    tables: dict[str, Any] = {}

    for table_name, table_meta in catalog["tables"].items():
        print(f"[dama] Processing '{table_name}' ...", end=" ", flush=True)
        path = staging_dir / f"{table_name}.parquet"
        if not path.exists():
            print("skipped (no staging data)")
            tables[table_name] = {"error": f"Table '{table_name}' not found in staging: {path.name}"}
            continue

        df = pd.read_parquet(path)
        contract = load_contract(table_name, contracts_dir)
        tables[table_name] = score_table(df, contract, table_meta, reference_values)
        print("ok")

    scorecard = {
        "generated_at": generated_at,
        "environment": settings["environment"],
        "layer": "staging",
        "tables": tables,
    }

    safe_ts = generated_at.replace(":", "-")
    json_path = dq_reports_dir / f"dama_scorecard_{safe_ts}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(scorecard, f, indent=2, ensure_ascii=False)

    md_path = dq_reports_dir / f"dama_scorecard_{safe_ts}.md"
    _write_markdown_report(scorecard, md_path)

    print(f"\n[dama] scorecard saved to: {json_path.relative_to(PROJECT_ROOT)}")
    print(f"[dama] human-readable report saved to: {md_path.relative_to(PROJECT_ROOT)}")

    return scorecard


def _fmt(pct: float | None, warn: bool = True) -> str:
    """Formats a score for the report: '-' when the dimension does not apply."""
    if pct is None:
        return "-"
    flag = " ⚠️" if warn and pct < 100 else ""
    return f"{pct:.2f}%{flag}"


def _write_markdown_report(scorecard: dict[str, Any], path: Path) -> None:
    """Formats the DAMA quality dimensions scorecard into a structured Markdown report."""
    lines = [
        "# DAMA UK Data Quality Dimensions Scorecard",
        "",
        f"- Generated at: {scorecard['generated_at']}",
        f"- Environment: {scorecard['environment']}",
        f"- Layer: {scorecard['layer']}",
        "",
        "Each cell is the worst column (or relation) of the table. `-` = dimension does not apply.",
        "",
        "| Table | Rows | Completeness (required) | Uniqueness (PK) | Validity | Consistency (FK) |",
        "|---|---|---|---|---|---|",
    ]

    for table_name, data in scorecard["tables"].items():
        if "error" in data:
            lines.append(f"| {table_name} | - | - | - | - | - |")
            continue
        s = data["summary"]
        lines.append(
            f"| {table_name} | {data['n_rows']} | {_fmt(s['completeness_pct'])} | "
            f"{_fmt(s['uniqueness_pct'])} | {_fmt(s['validity_pct'])} | {_fmt(s['consistency_pct'])} |"
        )

    lines.append("")
    lines.append("## Details per Table")

    for table_name, data in scorecard["tables"].items():
        lines.append(f"\n### {table_name}")
        if "error" in data:
            lines.append(f"- ⚠️ {data['error']}")
            continue

        optional = set(data["optional_columns"])
        lines.append("\n**Completeness per Column:**")
        for col, pct in data["completeness"].items():
            note = " (optional)" if col in optional else ""
            lines.append(f"- `{col}`{note}: {_fmt(pct, warn=col not in optional)}")

        if data["validity"]:
            lines.append("\n**Validity per Column:**")
            for col, pct in data["validity"].items():
                lines.append(f"- `{col}`: {_fmt(pct)}")

        if data["consistency"]:
            lines.append("\n**Consistency (Referential Integrity):**")
            for relation, pct in data["consistency"].items():
                lines.append(f"- `{relation}`: {_fmt(pct)}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    run()
