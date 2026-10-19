"""
Ingestion with contract validation: reads the original CSVs in data/raw/, validates
each table against the contract declared in contracts/<table_name>.contract.yaml, and
only writes to data/staging/ (Parquet format, cast to the contract types) if the contract
passes. If a "blocking" violation occurs, the specific table is blocked (circuit breaker)
and does not proceed. The remaining tables continue to be processed normally.

Every successful ingestion also writes a metrics snapshot to reports/quality_history/
(src.monitoring.history_store) - this serves as the historical baseline used by the
observability pillars (freshness/volume/schema/distribution) to detect Z-Score deviations
in future runs.

It also generates a human-readable Markdown report per execution in reports/dq_scorecards/

The process exits with code 1 if any table fails, so an orchestrator can react to it.

Usage:
    poetry run python -m src.ingestion.load_raw
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.config import PROJECT_ROOT, load_catalog, load_settings
from src.ingestion.contract_validator import load_contract, validate_dataframe
from src.ingestion.type_casting import cast_to_contract
from src.monitoring.history_store import append_snapshot, build_snapshot


def ingest_table(
    table_name: str,
    table_meta: dict[str, Any],
    raw_dir: Path,
    staging_dir: Path,
    contracts_dir: Path,
    history_dir: Path,
) -> dict[str, Any]:
    """
    Ingests a raw CSV table, validates it against its contract, writes it to Parquet
    (cast to the contract types) if approved, and records a historical metrics snapshot
    for observability tracking.

    Args:
        table_name: name of the target table
        table_meta: metadata dictionary containing source file configuration (catalog/inventory.yaml)
        raw_dir: directory containing raw CSV files
        staging_dir: destination directory for validated Parquet files
        contracts_dir: directory containing table contract YAML definitions
        history_dir: directory where metric history snapshots are stored

    Returns:
        A dictionary containing the ingestion execution results, status, metrics, and
        contract validation details.
    """
    file_name = table_meta["file"]
    raw_file = raw_dir / file_name

    result: dict[str, Any] = {
        "table": table_name,
        "source_file": file_name,
        "status": "ok",
        "error": None,
        "n_rows": None,
        "n_columns": None,
        "columns": None,
        "dtypes": None,
        "contract_passed": None,
        "contract_violations": []
    }

    if not raw_file.exists():
        result["status"] = "missing_file"
        result["error"] = f"File not found: {raw_file}"
        return result

    try:
        df = pd.read_csv(raw_file)
    except (OSError, ValueError) as e:  # I/O, encoding and CSV parsing failures
        result["status"] = "read_error"
        result["error"] = str(e)
        return result

    result["n_rows"] = len(df)
    result["n_columns"] = len(df.columns)
    result["columns"] = list(df.columns)
    result["dtypes"] = {col: str(dtype) for col, dtype in df.dtypes.items()}

    ### contract validator - per-table circuit breaker
    contract = load_contract(table_name, contracts_dir)
    validation = validate_dataframe(df, contract)
    result["contract_passed"] = validation.passed
    result["contract_violations"] = validation.as_dict()["violations"]

    if not validation.passed:
        result["status"] = "contract_violation"
        result["error"] = (
            f"{len(validation.violations)} contract violations - data blocked "
            "from moving to staging (circuit breaker tripped for this table)."
        )
        return result

    # staging is the typed layer: apply the contract types before writing
    typed_df = cast_to_contract(df, contract)

    staging_dir.mkdir(parents=True, exist_ok=True)
    out_path = staging_dir / f"{table_name}.parquet"
    typed_df.to_parquet(out_path, index=False)
    result["staging_path"] = out_path.relative_to(PROJECT_ROOT).as_posix()

    # historical snapshot
    snapshot = build_snapshot(table_name, typed_df)
    append_snapshot(snapshot, history_dir)

    return result


def generate_markdown_report(log_payload: dict[str, Any], report_path: Path) -> None:
    """
    Generates a human-readable Markdown scorecard report for a pipeline execution run.

    Formats ingestion summaries, table statuses, row counts, contract validation results,
    and detailed violation lists into a structured Markdown document.

    Args:
        log_payload: dictionary containing the execution run logs and results
        report_path: destination file path where the markdown report will be written
    """
    lines = [
        "# Ingestion & Contract Validation Report",
        "",
        f"- Started at: {log_payload['run_started_at']}",
        f"- Finished at: {log_payload['run_finished_at']}",
        f"- Environment: {log_payload['environment']}",
        "",
        "| Table | Status | Rows | Contract passed? | Violations |",
        "|---|---|---|---|---|",
    ]

    for r in log_payload["results"]:
        n_violations = len(r.get("contract_violations") or [])
        lines.append(
            f"| {r['table']} | {r['status']} | {r.get('n_rows', '-')} | "
            f"{r.get('contract_passed', '-')} | {n_violations} |"
        )

    lines.append("")
    lines.append("## Violation Details")

    for r in log_payload["results"]:
        violations = r.get("contract_violations") or []
        if not violations:
            continue

        lines.append(f"\n### {r['table']}")
        for v in violations:
            lines.append(f"- **[{v['severity']}]** `{v['rule']}` - column `{v['column']}`: {v['detail']}")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run() -> list[dict[str, Any]]:
    settings = load_settings()
    catalog = load_catalog(PROJECT_ROOT / settings["catalog_path"])

    raw_dir = PROJECT_ROOT / settings["raw_path"]
    staging_dir = PROJECT_ROOT / settings["staging_path"]
    logs_dir = PROJECT_ROOT / settings["ingestion_logs_path"]
    contracts_dir = PROJECT_ROOT / settings["contracts_path"]
    dq_reports_dir = PROJECT_ROOT / settings["dq_reports_path"]
    history_dir = PROJECT_ROOT / settings["quality_history_path"]

    logs_dir.mkdir(parents=True, exist_ok=True)
    dq_reports_dir.mkdir(parents=True, exist_ok=True)

    run_started_at = datetime.now(UTC).isoformat()
    results = []

    for table_name, table_meta in catalog["tables"].items():
        print(f"[ingestion] Processing '{table_name}' ...", end=" ", flush=True)
        outcome = ingest_table(table_name, table_meta, raw_dir, staging_dir, contracts_dir, history_dir)
        print(outcome["status"])
        results.append(outcome)

    run_finished_at = datetime.now(UTC).isoformat()

    log_payload = {
        "run_started_at": run_started_at,
        "run_finished_at": run_finished_at,
        "environment": settings["environment"],
        "results": results
    }

    safe_ts = run_started_at.replace(":", "-")
    log_file = logs_dir / f"run_{safe_ts}.json"
    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(log_payload, f, indent=2, ensure_ascii=False)

    report_file = dq_reports_dir / f"contract_report_{safe_ts}.md"
    generate_markdown_report(log_payload, report_file)

    n_ok = sum(1 for r in results if r["status"] == "ok")
    n_total = len(results)
    print(f"\n[ingestion] completed: {n_ok}/{n_total} tables processed successfully.")
    print(f"[ingestion] log saved to: {log_file.relative_to(PROJECT_ROOT)}")
    print(f"[ingestion] quality scorecard saved to: {report_file.relative_to(PROJECT_ROOT)}")

    if n_ok < n_total:
        print(
            "[ingestion] WARNING: one or more tables failed or violated their data contract. "
            "Check the quality report for details.",
            file=sys.stderr
        )

    return results


def main() -> int:
    """Runs the ingestion and returns a process exit code (0 = all tables ok, 1 = otherwise)."""
    results = run()
    return 0 if all(r["status"] == "ok" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
