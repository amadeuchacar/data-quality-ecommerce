"""
Local Orchestration

Single entry point that runs the whole pipeline sequentially:
  1. Ingestion + contract validation (raw -> staging, circuit breaker per table)
  2. DAMA quality dimensions scorecard on staging
  3. Observability pillars + SLOs + alerts + incidents
  4. Certification of every table
  5. Curated layer (only when its source tables passed their contract in THIS execution)
  6. Maturity dashboard

Steps 1-3 always run: a blocked table simply keeps its last snapshot, so the freshness pillar is
what surfaces it downstream. The pillars are evaluated once (step 3) and their findings are reused
by certification and the dashboard, so every report reads the same verdicts.

Exit code: 0 when every table was ingested and every SLO was met, 1 otherwise.

Usage:
    poetry run python -m orchestration.local_pipeline
"""

import sys

from src.certification.certify import run as run_certification
from src.ingestion.load_raw import run as run_ingestion
from src.quality.checks_runner import run as run_checks
from src.quality.dimensions_dama import run as run_dama
from src.reporting.maturity_dashboard import run as run_dashboard
from src.transform.build_curated import CURATED_SOURCES
from src.transform.build_curated import run as run_curated

N_STEPS = 6


def _banner(step: int, title: str) -> None:
    print("\n" + "=" * 60)
    print(f"STEP {step}/{N_STEPS} - {title}")
    print("=" * 60)


def run() -> int:
    """Executes the end-to-end local data pipeline and returns a process exit code."""
    _banner(1, "Ingestion + Contract Validation")
    ingestion_results = run_ingestion()
    ingested = {r["table"] for r in ingestion_results if r["status"] == "ok"}

    _banner(2, "DAMA Quality Dimensions (staging)")
    run_dama()

    _banner(3, "Observability Pillars + SLOs")
    findings_by_table, slo_results = run_checks()

    _banner(4, "Certification")
    certification_results = run_certification(findings_by_table)

    _banner(5, "Curated Layer + Field Lineage")
    missing = sorted(CURATED_SOURCES - ingested)
    if missing:
        print(
            f"[curated] SKIPPED: source tables blocked or missing in this ingestion: {missing}. "
            "The curated layer is rebuilt only from data that passed contract validation."
        )
    else:
        run_curated()

    _banner(6, "Maturity Dashboard")
    run_dashboard(findings_by_table, certification_results)

    blocked = sorted({r["table"] for r in ingestion_results} - ingested)
    breached = sorted(r.slo_id for r in slo_results if not r.passed)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Tables blocked at ingestion: {blocked or 'none'}")
    print(f"SLOs breached: {breached or 'none'}")
    print(f"Certified tables: {sum(r.certified for r in certification_results)}/{len(certification_results)}")

    return 0 if not blocked and not breached else 1


if __name__ == "__main__":
    sys.exit(run())
