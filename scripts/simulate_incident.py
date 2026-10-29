"""
Incident simulator - breaks the raw data on purpose to exercise each defense of the pipeline.

Each scenario changes ONE raw CSV in a way that a specific mechanism should catch:

    contract_violation  order_items: negative prices          -> contract blocks the table (circuit breaker)
    volume_drop         orders: last 8% of rows removed        -> passes the contract, volume pillar fires
    distribution_shift  order_items: freight_value x3          -> passes the contract, distribution pillar fires
    null_spike          products: 30% of categories nulled     -> passes the contract, null-rate shift fires
    schema_drift        order_reviews: unexpected new column   -> contract only warns, schema pillar fires

The first change backs up data/raw/ to data/raw_backup/; `restore` puts the original files back.
Statistical pillars need at least 3 previous executions: run the pipeline a few times BEFORE
simulating, so there is a baseline to deviate from.

Usage:
    poetry run python -m scripts.simulate_incident list
    poetry run python -m scripts.simulate_incident volume_drop
    poetry run python -m orchestration.local_pipeline
    poetry run python -m scripts.simulate_incident restore
"""

import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from src.config import PROJECT_ROOT, load_catalog, load_settings


@dataclass(frozen=True)
class Scenario:
    table: str
    expected: str
    transform: Callable[[pd.DataFrame], pd.DataFrame]


def _negative_prices(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.loc[df.index[:10], "price"] = -1.0
    return df


def _drop_last_rows(df: pd.DataFrame, fraction: float = 0.08) -> pd.DataFrame:
    return df.iloc[: int(len(df) * (1 - fraction))]


def _triple_freight(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["freight_value"] = df["freight_value"] * 3
    return df


def _null_categories(df: pd.DataFrame, fraction: float = 0.30) -> pd.DataFrame:
    df = df.copy()
    df.loc[df.index[: int(len(df) * fraction)], "product_category_name"] = None
    return df


def _add_column(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["review_channel"] = "app"
    return df


SCENARIOS: dict[str, Scenario] = {
    "contract_violation": Scenario(
        "order_items", "contract blocks order_items; curated layer is skipped; exit code 1", _negative_prices
    ),
    "volume_drop": Scenario(
        "orders", "volume anomaly (critical) on orders; SLO orders_volume_stability breached", _drop_last_rows
    ),
    "distribution_shift": Scenario(
        "order_items", "distribution anomaly on order_items; SLO order_items_distribution_stability breached",
        _triple_freight,
    ),
    "null_spike": Scenario(
        "products", "distribution anomaly (null rate) on products; certification lost", _null_categories
    ),
    "schema_drift": Scenario(
        "order_reviews", "contract warning only; schema anomaly; SLO order_reviews_schema_stability breached",
        _add_column,
    ),
}


def _paths() -> tuple:
    settings = load_settings()
    raw_dir = PROJECT_ROOT / settings["raw_path"]
    catalog = load_catalog(PROJECT_ROOT / settings["catalog_path"])
    return raw_dir, raw_dir.with_name(raw_dir.name + "_backup"), catalog


def simulate(name: str) -> None:
    """Backs up data/raw/ (once) and applies the scenario to its table's CSV."""
    scenario = SCENARIOS[name]
    raw_dir, backup_dir, catalog = _paths()
    if not backup_dir.exists():
        shutil.copytree(raw_dir, backup_dir)
        print(f"[simulate] original data backed up to {backup_dir.relative_to(PROJECT_ROOT)}")

    csv = raw_dir / catalog["tables"][scenario.table]["file"]
    scenario.transform(pd.read_csv(csv)).to_csv(csv, index=False)
    print(f"[simulate] '{name}' applied to {csv.name}")
    print(f"[simulate] expected: {scenario.expected}")


def restore() -> None:
    """Puts the original raw files back and removes the backup."""
    raw_dir, backup_dir, _ = _paths()
    if not backup_dir.exists():
        print("[simulate] nothing to restore (no backup found).")
        return
    shutil.rmtree(raw_dir)
    backup_dir.rename(raw_dir)
    print("[simulate] original raw data restored.")


def main(argv: list[str]) -> int:
    command = argv[0] if argv else "list"
    if command == "list":
        for name, scenario in SCENARIOS.items():
            print(f"{name:<20} {scenario.table:<14} {scenario.expected}")
        return 0
    if command == "restore":
        restore()
        return 0
    if command in SCENARIOS:
        simulate(command)
        return 0
    print(f"unknown command '{command}'. Use: list | restore | {' | '.join(SCENARIOS)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
