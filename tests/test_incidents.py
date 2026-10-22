"""Unit tests for incident logging (src.incidents.incident_log)."""

from datetime import UTC, datetime, timedelta

import pytest

from src.incidents.incident_log import (
    compute_ttd_seconds,
    compute_ttr_seconds,
    find_incident,
    load_incidents,
    register_incident,
    register_resolution,
)


def test_ttd_zero_when_detected_immediately():
    now = datetime.now(UTC)

    assert compute_ttd_seconds(occurred_at=now, detected_at=now) == 0.0


def test_ttd_matches_simulated_delay():
    detected = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

    assert compute_ttd_seconds(occurred_at=detected - timedelta(hours=2), detected_at=detected) == 7200.0


def test_ttr_matches_simulated_resolution_time():
    detected = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

    assert compute_ttr_seconds(detected_at=detected, resolved_at=detected + timedelta(minutes=30)) == 1800.0


def test_register_incident_persists_severity_and_snapshot(tmp_path):
    occurred = datetime.now(UTC) - timedelta(hours=1)
    incident = register_incident(
        "orders", "volume", "drop", ["order_items"], tmp_path,
        occurred_at=occurred, severity="critical", snapshot_at="2026-10-01T12:00:00+00:00",
    )

    stored = load_incidents(tmp_path)
    assert stored == [incident.as_dict()]
    assert stored[0]["severity"] == "critical"
    assert 3599 <= stored[0]["ttd_seconds"] <= 3601


def test_register_resolution_recovers_detected_at_from_incident_file(tmp_path):
    incident = register_incident("order_items", "distribution", "test", [], tmp_path)
    resolution = register_resolution(
        incident.incident_id, "fixed", tmp_path, resolved_at=datetime.now(UTC) + timedelta(minutes=10)
    )

    assert 599 <= resolution.ttr_seconds <= 601


def test_register_resolution_unknown_incident_raises(tmp_path):
    with pytest.raises(ValueError):
        register_resolution("non-existent", "x", tmp_path)


def test_find_incident_matches_table_pillar_and_snapshot(tmp_path):
    register_incident("orders", "volume", "drop", [], tmp_path, snapshot_at="snap-1")
    incidents = load_incidents(tmp_path)

    assert find_incident(incidents, "orders", "volume", "snap-1") is not None
    assert find_incident(incidents, "orders", "volume", "snap-2") is None        # new data
    assert find_incident(incidents, "orders", "schema", "snap-1") is None        # other pillar
    assert find_incident(incidents, "orders", "volume", None) is None            # no snapshot: never dedupe
