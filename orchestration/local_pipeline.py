"""
Local Orchestration

Single entry point that runs the whole pipeline sequentially:
  1. Ingestion + contract validation (raw -> staging, circuit breaker per table)
  2. DAMA quality dimensions scorecard on staging
  3. Observability pillars + SLOs + alerts + incidents

Every step runs even if the previous one blocked some tables: a blocked table simply keeps
its last snapshot, so the freshness pillar is what surfaces it downstream.

Exit code: 0 when every table was ingested and every SLO was met, 1 otherwise.

Usage:
    poetry run python -m orchestration.local_pipeline
"""

import sys

from src.ingestion.load_raw import run as run_ingestion
from src.quality.checks_runner import run as run_checks
from src.quality.dimensions_dama import run as run_dama


def _banner(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def run() -> int:
    """Executes the end-to-end local data pipeline and returns a process exit code."""
    _banner("STEP 1/3 - Ingestion + Contract Validation")
    ingestion_results = run_ingestion()

    _banner("STEP 2/3 - DAMA Quality Dimensions (staging)")
    run_dama()

    _banner("STEP 3/3 - Observability Pillars + SLOs")
    _, slo_results = run_checks()

    blocked = sorted(r["table"] for r in ingestion_results if r["status"] != "ok")
    breached = sorted(r.slo_id for r in slo_results if not r.passed)

    _banner("SUMMARY")
    print(f"Tables blocked at ingestion: {blocked or 'none'}")
    print(f"SLOs breached: {breached or 'none'}")

    return 0 if not blocked and not breached else 1


if __name__ == "__main__":
    sys.exit(run())
