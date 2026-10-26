"""Unit tests for data certification (src.certification.certify)."""

import pytest
import yaml

from src.certification.certify import certify_all, certify_table, write_status
from src.quality.pillars.base import PillarFinding


def _findings(**statuses: str) -> dict[str, PillarFinding]:
    """Findings for the four pillars, 'ok' unless overridden (e.g. volume='anomaly')."""
    pillars = ("freshness", "volume", "schema", "distribution")
    return {p: PillarFinding("orders", p, statuses.get(p, "ok"), f"{p} detail") for p in pillars}


def test_healthy_table_is_certified_with_default_owner():
    result = certify_table("orders", {"owner": None}, "default@x.com", _findings(), [])

    assert result.certified
    assert result.owner == "default@x.com"
    assert result.reasons == []


def test_specific_owner_wins_over_default():
    assert certify_table("orders", {"owner": "me@x.com"}, "default@x.com", _findings(), []).owner == "me@x.com"


def test_not_certified_without_owner():
    result = certify_table("orders", {"owner": None}, None, _findings(), [])

    assert not result.certified
    assert "owner" in result.reasons[0]


def test_not_certified_without_history():
    result = certify_table("orders", {"owner": "x"}, None, {}, [])

    assert not result.certified
    assert "No ingestion history" in result.reasons[0]


@pytest.mark.parametrize("status", ["anomaly", "insufficient_history"])
def test_not_certified_when_a_pillar_is_not_ok(status):
    result = certify_table("orders", {"owner": "x"}, None, _findings(volume=status), [])

    assert not result.certified
    assert any("volume" in reason for reason in result.reasons)


def test_not_certified_with_an_open_incident():
    incident = {"incident_id": "abc", "pillar": "volume", "table": "orders"}

    result = certify_table("orders", {"owner": "x"}, None, _findings(), [incident])

    assert not result.certified
    assert "abc" in result.reasons[0]


def test_certify_all_routes_open_incidents_to_their_table():
    catalog = {"owner_default": "team", "tables": {"orders": {}, "sellers": {}}}
    findings = {"orders": _findings(), "sellers": _findings()}
    unresolved = [{"incident_id": "abc", "pillar": "schema", "table": "sellers"}]

    results = {r.table: r for r in certify_all(catalog, findings, unresolved)}

    assert results["orders"].certified
    assert not results["sellers"].certified


def test_write_status_persists_every_table(tmp_path):
    results = certify_all({"owner_default": "team", "tables": {"orders": {}}}, {"orders": _findings()}, [])
    path = tmp_path / "certification" / "certification_status.yaml"

    write_status(results, path)

    status = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert status["tables"]["orders"]["certified"] is True
    assert "generated_at" in status
