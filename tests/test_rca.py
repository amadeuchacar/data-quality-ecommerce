"""Unit tests for RCA, postmortem (src.incidents.rca) and the incident CLI (src.incidents.cli)."""

import json

import pytest

from src.incidents import cli
from src.incidents.incident_log import load_resolutions, register_incident
from src.incidents.rca import RCA, load_rca_for_incident, render_postmortem, save_postmortem, save_rca

INCIDENT = {
    "incident_id": "abc-123",
    "table": "order_items",
    "pillar": "distribution",
    "detected_at": "2026-09-30T22:16:02+00:00",
    "ttd_seconds": 0.0,
    "affected_tables": [],
}


# --- RCA and postmortem -----------------------------------------------------------------

def test_rca_roundtrip_and_postmortem_rendering(tmp_path):
    save_rca(RCA(incident_id="abc-123", whys=["a", "b", "c"], root_cause="root_cause_val", conducted_by="me"), tmp_path)
    loaded = load_rca_for_incident("abc-123", tmp_path)

    assert loaded is not None and loaded["root_cause"] == "root_cause_val"
    json.dumps(loaded)      # serializable

    md = render_postmortem(INCIDENT, loaded, ["event 1"], "impact_val", ["action 1"])
    assert "Blameless" in md
    assert "root_cause_val" in md and "action 1" in md and "3. c" in md


def test_latest_rca_wins_when_an_investigation_is_redone(tmp_path):
    save_rca(RCA("abc-123", ["first"], "first cause", "me"), tmp_path)
    save_rca(RCA("abc-123", ["second"], "second cause", "me"), tmp_path)

    assert load_rca_for_incident("abc-123", tmp_path)["root_cause"] == "second cause"


def test_missing_rca_returns_none(tmp_path):
    assert load_rca_for_incident("nope", tmp_path) is None


def test_save_postmortem_writes_markdown_file(tmp_path):
    path = save_postmortem("abc-123", "# Postmortem\n", tmp_path)

    assert path == tmp_path / "postmortems" / "abc-123.md"
    assert path.read_text(encoding="utf-8") == "# Postmortem\n"


# --- CLI --------------------------------------------------------------------------------

@pytest.fixture
def incidents_dir(tmp_path, monkeypatch):
    """Points the CLI at a temporary incidents directory."""
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: {"incidents_path": "incidents"})
    return tmp_path / "incidents"


def test_find_by_prefix():
    incidents = [{"incident_id": "abc-1"}, {"incident_id": "abd-2"}]

    assert cli.find_by_prefix("abc", incidents)["incident_id"] == "abc-1"
    with pytest.raises(ValueError, match="ambiguous"):
        cli.find_by_prefix("ab", incidents)
    with pytest.raises(ValueError, match="No incident"):
        cli.find_by_prefix("zz", incidents)


def test_cli_resolve_then_list_shows_no_open_incident(incidents_dir, capsys):
    incident = register_incident("orders", "volume", "drop", [], incidents_dir)

    assert cli.main(["resolve", incident.incident_id[:8], "--summary", "re-ran export"]) == 0
    assert load_resolutions(incidents_dir)[0]["incident_id"] == incident.incident_id

    cli.main(["list"])
    assert "No open incidents." in capsys.readouterr().out


def test_cli_rejects_resolving_twice(incidents_dir):
    incident = register_incident("orders", "volume", "drop", [], incidents_dir)
    cli.main(["resolve", incident.incident_id, "--summary", "fixed"])

    assert cli.main(["resolve", incident.incident_id, "--summary", "fixed again"]) == 1


def test_cli_rca_and_postmortem(incidents_dir):
    incident = register_incident("orders", "volume", "drop", ["order_items"], incidents_dir)
    prefix = incident.incident_id[:8]

    assert cli.main(["postmortem", prefix, "--impact", "x"]) == 1      # RCA is required first
    assert cli.main(["rca", prefix, "--why", "w1", "--why", "w2", "--root-cause", "rc", "--by", "me"]) == 0
    assert cli.main(["postmortem", prefix, "--impact", "revenue", "--action", "add check"]) == 0

    postmortem = (incidents_dir / "postmortems" / f"{incident.incident_id}.md").read_text(encoding="utf-8")
    assert "revenue" in postmortem and "add check" in postmortem and "order_items" in postmortem
