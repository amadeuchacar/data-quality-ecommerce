"""Unit tests for the maturity dashboard KPIs (src.reporting.maturity_dashboard)."""

from src.certification.certify import CertificationResult
from src.quality.pillars.base import PillarFinding
from src.quality.sli_slo import SLOResult
from src.reporting.maturity_dashboard import _fmt_duration, compute_kpis, render_dashboard_markdown


def _slo(passed: bool) -> SLOResult:
    finding = PillarFinding("orders", "volume", "ok" if passed else "anomaly", "d")
    return SLOResult("slo", "orders", "volume", "desc", passed, finding)


INCIDENTS = [
    {"incident_id": "i1", "ttd_seconds": 60.0, "severity": "critical"},
    {"incident_id": "i2", "ttd_seconds": 180.0, "severity": "warning"},
    {"incident_id": "i3", "ttd_seconds": 120.0, "severity": "warning"},
]
RESOLUTIONS = [{"incident_id": "i1", "ttr_seconds": 3600.0}]
CERTIFICATION = [
    CertificationResult("orders", True, "team", []),
    CertificationResult("sellers", False, "team", ["reason"]),
]


def test_compute_kpis():
    kpis = compute_kpis(INCIDENTS, RESOLUTIONS, [_slo(True), _slo(False)], CERTIFICATION)

    assert kpis["n_incidents"] == 3
    assert kpis["n_open"] == 2
    assert kpis["n_resolved"] == 1
    assert kpis["pct_resolved"] == 33.3
    assert kpis["incidents_by_severity"] == {"critical": 1, "warning": 2}
    assert kpis["ttd_mean_seconds"] == 120.0
    assert kpis["ttr_mean_seconds"] == 3600.0
    assert kpis["pct_slo_ok"] == 50.0
    assert kpis["pct_certified"] == 50.0


def test_resolutions_of_unknown_incidents_are_ignored():
    kpis = compute_kpis(INCIDENTS, [*RESOLUTIONS, {"incident_id": "ghost", "ttr_seconds": 1.0}], [], [])

    assert kpis["n_resolved"] == 1
    assert kpis["ttr_mean_seconds"] == 3600.0


def test_empty_inputs_give_not_applicable_values():
    kpis = compute_kpis([], [], [], [])

    assert kpis["pct_resolved"] is None
    assert kpis["ttd_mean_seconds"] is None
    assert kpis["pct_slo_ok"] is None


def test_fmt_duration_picks_a_readable_unit():
    assert _fmt_duration(None).startswith("N/A")
    assert _fmt_duration(30) == "30.0s"
    assert _fmt_duration(90) == "1.5min"
    assert _fmt_duration(5400) == "1.5h"


def test_render_dashboard_markdown():
    dashboard = {
        "generated_at": "2026-10-22T00:00:00+00:00",
        **compute_kpis(INCIDENTS, RESOLUTIONS, [_slo(True)], CERTIFICATION),
        "slo_results": [_slo(True)],
        "certification_results": CERTIFICATION,
    }

    md = render_dashboard_markdown(dashboard)

    assert "| Open incidents | 2 |" in md
    assert "| Mean TTR (time to resolution) | 1.0h |" in md
    assert "| sellers | ❌ | reason |" in md
