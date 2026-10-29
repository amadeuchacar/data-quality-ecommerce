"""Unit tests for the incident simulator scenarios (scripts.simulate_incident)."""

import pandas as pd
import pytest

from scripts.simulate_incident import SCENARIOS, main


@pytest.fixture
def frames() -> dict[str, pd.DataFrame]:
    return {
        "order_items": pd.DataFrame({"price": [10.0] * 20, "freight_value": [2.0] * 20}),
        "orders": pd.DataFrame({"order_id": [str(i) for i in range(100)]}),
        "products": pd.DataFrame({"product_category_name": ["toys"] * 10}),
        "order_reviews": pd.DataFrame({"review_id": ["r1", "r2"]}),
    }


def _apply(name: str, frames: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    original = frames[SCENARIOS[name].table]
    return original, SCENARIOS[name].transform(original)


def test_contract_violation_makes_prices_negative(frames):
    original, changed = _apply("contract_violation", frames)

    assert (changed["price"] < 0).sum() == 10
    assert (original["price"] > 0).all()          # input not mutated


def test_volume_drop_removes_eight_percent(frames):
    _, changed = _apply("volume_drop", frames)

    assert len(changed) == 92


def test_distribution_shift_triples_freight(frames):
    _, changed = _apply("distribution_shift", frames)

    assert changed["freight_value"].tolist() == [6.0] * 20


def test_null_spike_nulls_thirty_percent(frames):
    _, changed = _apply("null_spike", frames)

    assert changed["product_category_name"].isna().sum() == 3


def test_schema_drift_adds_a_column(frames):
    original, changed = _apply("schema_drift", frames)

    assert set(changed.columns) - set(original.columns) == {"review_channel"}


def test_every_scenario_targets_a_cataloged_table():
    import yaml

    from src.config import PROJECT_ROOT

    tables = yaml.safe_load((PROJECT_ROOT / "catalog" / "inventory.yaml").read_text(encoding="utf-8"))["tables"]
    assert {s.table for s in SCENARIOS.values()} <= set(tables)


def test_unknown_command_returns_error_code():
    assert main(["not_a_scenario"]) == 1
