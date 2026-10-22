"""Unit tests for the observability pillars (src.quality.pillars)."""

from datetime import timedelta

from src.quality.pillars.distribution import check_distribution
from src.quality.pillars.freshness import check_freshness
from src.quality.pillars.lineage import build_lineage_graph, downstream_impact, upstream_dependencies
from src.quality.pillars.schema import check_schema
from src.quality.pillars.volume import check_volume
from tests.conftest import NOW, stable_history

# --- Freshness --------------------------------------------------------------------------

def test_freshness_ok_when_recent(history):
    assert check_freshness("orders", history, max_delay_hours=24, now=NOW + timedelta(hours=2)).status == "ok"


def test_freshness_anomaly_when_stale(history):
    finding = check_freshness("orders", history, max_delay_hours=24, now=NOW + timedelta(hours=48))

    assert finding.status == "anomaly"
    assert "48.0h" in finding.detail


def test_freshness_insufficient_history_when_empty():
    assert check_freshness("orders", [], max_delay_hours=24).status == "insufficient_history"


# --- Volume -----------------------------------------------------------------------------

def test_volume_ok_within_history(history):
    assert check_volume("orders", 100_000, history, min_expected_volume=90_000).status == "ok"


def test_volume_anomaly_on_sharp_drop(history):
    history.append({**history[-1], "n_rows": 500})

    assert check_volume("orders", 500, history, min_expected_volume=90_000).status == "anomaly"


def test_volume_fallback_to_contract_when_insufficient_history():
    history = stable_history(n=1)

    assert check_volume("orders", 100, history, min_expected_volume=90_000).status == "anomaly"


def test_volume_moving_method_catches_drop_after_contaminated_history():
    history = stable_history(n=6, n_rows=99441)
    history[2]["n_rows"] = 5_000_000                       # old outlier contaminating the baseline
    history.append({**history[-1], "n_rows": 50_000})      # current execution: abrupt drop

    assert check_volume("orders", 50_000, history, method="moving").status == "anomaly"


# --- Schema -----------------------------------------------------------------------------

def test_schema_ok_when_unchanged(history):
    assert check_schema("orders", history).status == "ok"


def test_schema_anomaly_on_new_column(history):
    history.append(
        {**history[-1], "columns": ["a", "b", "c"], "dtypes": {"a": "object", "b": "float64", "c": "int64"}}
    )
    finding = check_schema("orders", history)

    assert finding.status == "anomaly"
    assert "c" in finding.detail


def test_schema_anomaly_on_type_change(history):
    history.append({**history[-1], "dtypes": {"a": "object", "b": "str"}})

    assert "changed data types" in check_schema("orders", history).detail


# --- Distribution -----------------------------------------------------------------------

def test_distribution_anomaly_on_mean_shift():
    history = stable_history(mean=50.0)
    history.append({**history[-1], "numeric_stats": {"b": {"mean": 500.0, "std": 2.0}}})

    assert check_distribution("orders", history).status == "anomaly"


def test_distribution_insufficient_history():
    assert check_distribution("orders", stable_history(n=1)).status == "insufficient_history"


def test_distribution_moving_catches_distortion_that_global_zscore_misses():
    history = stable_history(n=7, mean=120.0)
    history[1]["numeric_stats"]["b"]["mean"] = 6.0e8       # old incident in history
    history.append({**history[-1], "numeric_stats": {"b": {"mean": 120.0 * 50, "std": 2.0}}})

    assert check_distribution("order_items", history, method="moving").status == "anomaly"
    assert check_distribution("order_items", history, method="zscore").status != "anomaly"


# --- Lineage (table-level) --------------------------------------------------------------

CATALOG = {
    "tables": {
        "customers": {"foreign_keys": []},
        "orders": {"foreign_keys": [{"column": "customer_id", "references": "customers.customer_id"}]},
        "order_items": {
            "foreign_keys": [
                {"column": "order_id", "references": "orders.order_id"},
                {"column": "order_id_copy", "references": "orders.order_id"},     # same pair twice
            ]
        },
    }
}


def test_lineage_downstream_impact_is_transitive():
    graph = build_lineage_graph(CATALOG)

    assert downstream_impact("customers", graph) == ["order_items", "orders"]
    assert upstream_dependencies("order_items", graph) == ["customers", "orders"]


def test_lineage_lists_each_edge_once():
    graph = build_lineage_graph(CATALOG)

    assert graph["orders"]["downstream"] == ["order_items"]


def test_lineage_of_leaf_and_unknown_tables_is_empty():
    graph = build_lineage_graph(CATALOG)

    assert downstream_impact("order_items", graph) == []
    assert downstream_impact("not_a_table", graph) == []
