"""
Unit tests for SLO evaluation (src.quality.sli_slo) and consistency tests between
config/slo.yaml and the catalog.
"""

from pathlib import Path

import pytest
import yaml

from src.quality.pillars.base import PillarFinding
from src.quality.sli_slo import (
    DEFAULT_PILLAR_PARAMS,
    PILLARS,
    evaluate_all,
    evaluate_slos,
    load_slos,
    pillar_params,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SLO_PATH = PROJECT_ROOT / "config" / "slo.yaml"

SLOS = [
    {
        "id": "products_freshness", "table": "products", "pillar": "freshness",
        "description": "48h  tolerance", "max_delay_hours": 48,
    },
    {
        "id": "orders_volume", "table": "orders", "pillar": "volume",
        "description": "volume", "z_threshold": 4.0, "method": "ewma", "window": 5,
    },
]


# --- Parameter resolution ---------------------------------------------------------------

def test_pillar_params_use_defaults_without_slo():
    assert pillar_params("orders", "freshness", SLOS) == DEFAULT_PILLAR_PARAMS["freshness"]


def test_pillar_params_are_overridden_by_the_slo():
    assert pillar_params("products", "freshness", SLOS) == {"max_delay_hours": 48}
    assert pillar_params("orders", "volume", SLOS) == {"z_threshold": 4.0, "method": "ewma", "window": 5}


def test_pillar_params_do_not_leak_slo_metadata():
    assert not {"id", "table", "pillar", "description"} & set(pillar_params("orders", "volume", SLOS))


def test_pillar_params_do_not_mutate_defaults():
    pillar_params("products", "freshness", SLOS)

    assert DEFAULT_PILLAR_PARAMS["freshness"] == {"max_delay_hours": 24.0}


# --- SLO results ------------------------------------------------------------------------

def test_slo_reads_the_verdict_of_the_existing_finding():
    findings = {"products": {"freshness": PillarFinding("products", "freshness", "anomaly", "stale")}}

    result = evaluate_slos(SLOS[:1], findings)[0]

    assert not result.passed
    assert result.finding.detail == "stale"
    assert result.description == "48h tolerance"     # whitespace normalized


def test_slo_without_finding_is_insufficient_history_not_a_failure():
    result = evaluate_slos(SLOS[:1], {})[0]

    assert result.passed
    assert result.finding.status == "insufficient_history"


def test_slo_volume_with_empty_history_is_not_a_false_anomaly():
    contracts = {"orders": {"sla": {"min_expected_volume": 90_000}}}

    result = evaluate_all({"orders": []}, contracts, SLOS[1:])[0]

    assert result.passed
    assert result.finding.status == "insufficient_history"


def test_load_slos_rejects_unknown_pillar(tmp_path):
    path = tmp_path / "slo.yaml"
    path.write_text(yaml.safe_dump({"slos": [{"id": "x", "table": "t", "pillar": "magic", "description": "d"}]}))

    with pytest.raises(ValueError):
        load_slos(path)


# --- config/slo.yaml consistency --------------------------------------------------------

PROJECT_SLOS = load_slos(SLO_PATH)
CATALOG_TABLES = set(
    yaml.safe_load((PROJECT_ROOT / "catalog" / "inventory.yaml").read_text(encoding="utf-8"))["tables"]
)


def test_slo_ids_are_unique():
    ids = [s["id"] for s in PROJECT_SLOS]

    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("slo", PROJECT_SLOS, ids=lambda s: s["id"])
def test_slo_points_to_a_cataloged_table_and_known_pillar(slo):
    assert slo["table"] in CATALOG_TABLES
    assert slo["pillar"] in PILLARS


@pytest.mark.parametrize("slo", PROJECT_SLOS, ids=lambda s: s["id"])
def test_slo_only_sets_parameters_its_pillar_understands(slo):
    params = set(pillar_params(slo["table"], slo["pillar"], [slo]))

    assert params <= set(DEFAULT_PILLAR_PARAMS[slo["pillar"]])


def test_statistical_slos_use_the_moving_method():
    stat = [s for s in PROJECT_SLOS if s["pillar"] in ("volume", "distribution")]

    assert stat
    assert all(s.get("method") == "moving" and s.get("window") for s in stat)
