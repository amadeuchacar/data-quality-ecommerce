"""Unit tests for the anomaly handling of the checks runner (src.quality.checks_runner)."""

from datetime import timedelta

from src.incidents.incident_log import load_incidents
from src.quality.checks_runner import handle_anomaly, occurrence_time
from src.quality.pillars.base import PillarFinding
from src.quality.pillars.lineage import build_lineage_graph
from tests.conftest import NOW

SNAPSHOT_AT = NOW.isoformat()
GRAPH = build_lineage_graph(
    {
        "tables": {
            "orders": {"foreign_keys": []},
            "order_items": {"foreign_keys": [{"column": "order_id", "references": "orders.order_id"}]},
        }
    }
)


def _volume_anomaly() -> PillarFinding:
    return PillarFinding("orders", "volume", "anomaly", "500 rows")


def test_occurrence_time_is_the_snapshot_for_data_pillars():
    assert occurrence_time(_volume_anomaly(), SNAPSHOT_AT, {}) == NOW


def test_occurrence_time_for_freshness_is_when_the_slo_was_crossed():
    finding = PillarFinding("orders", "freshness", "anomaly", "stale")

    assert occurrence_time(finding, SNAPSHOT_AT, {"max_delay_hours": 24}) == NOW + timedelta(hours=24)


def test_handle_anomaly_registers_incident_with_impact_and_severity(tmp_path):
    known: list = []

    handle_anomaly(_volume_anomaly(), {"criticality": "high"}, SNAPSHOT_AT, {}, GRAPH, known,
                   tmp_path / "alerts", tmp_path / "incidents")

    stored = load_incidents(tmp_path / "incidents")
    assert len(stored) == 1
    assert stored[0]["severity"] == "critical"
    assert stored[0]["affected_tables"] == ["order_items"]
    assert stored[0]["snapshot_at"] == SNAPSHOT_AT
    assert (tmp_path / "alerts" / "alerts.jsonl").exists()


def test_handle_anomaly_does_not_duplicate_the_same_snapshot(tmp_path):
    known: list = []
    args = ({"criticality": "low"}, SNAPSHOT_AT, {}, GRAPH, known, tmp_path / "alerts", tmp_path / "incidents")

    handle_anomaly(_volume_anomaly(), *args)
    handle_anomaly(_volume_anomaly(), *args)     # same anomaly, re-run on unchanged data

    stored = load_incidents(tmp_path / "incidents")
    assert len(stored) == 1
    assert stored[0]["severity"] == "info"
    assert len((tmp_path / "alerts" / "alerts.jsonl").read_text(encoding="utf-8").splitlines()) == 1
