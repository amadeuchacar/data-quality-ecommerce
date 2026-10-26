"""
Stage 4 — Analytical Transformations

Builds the curated layer (data/curated/) from the typed staging layer. Each derived column
has a corresponding entry in the field lineage registry (src/lineage/field_lineage.py).

The transformations are pure functions (DataFrame in, DataFrame out); run() only handles
reading staging and writing Parquet. The orchestrator calls run() only when the source tables
passed their contract in the same execution, so curated is never rebuilt from stale staging.

Usage:
    poetry run python -m src.transform.build_curated
"""

import pandas as pd

from src.config import PROJECT_ROOT, load_settings

# Staging tables each curated table is built from (used by the orchestrator's gate)
CURATED_SOURCES = {"order_items", "orders"}


def curate_order_items(order_items: pd.DataFrame) -> pd.DataFrame:
    """Adds item_total_value = price + freight_value (the value of ONE item, not of the order)."""
    df = order_items.copy()
    df["item_total_value"] = df["price"] + df["freight_value"]
    return df


def curate_orders(orders: pd.DataFrame, order_items_curated: pd.DataFrame) -> pd.DataFrame:
    """
    Adds to each order:
    - delivery_delay_days: delivered - estimated, in days (null while not delivered);
    - order_total_value: sum of item_total_value of its items (null for orders without items,
      e.g. canceled before invoicing - "no items" is not the same as "zero value").
    """
    df = orders.copy()
    delay = df["order_delivered_customer_date"] - df["order_estimated_delivery_date"]
    df["delivery_delay_days"] = delay.dt.days.astype("Int64")

    totals = order_items_curated.groupby("order_id", as_index=False)["item_total_value"].sum()
    totals = totals.rename(columns={"item_total_value": "order_total_value"})
    return df.merge(totals, on="order_id", how="left", validate="one_to_one")


def run() -> dict[str, int]:
    """Builds every curated table from staging and returns the row count of each."""
    settings = load_settings()
    staging_dir = PROJECT_ROOT / settings["staging_path"]
    curated_dir = PROJECT_ROOT / settings["curated_path"]
    curated_dir.mkdir(parents=True, exist_ok=True)

    print("[build_curated] order_items -> item_total_value ...", end=" ", flush=True)
    order_items_curated = curate_order_items(pd.read_parquet(staging_dir / "order_items.parquet"))
    order_items_curated.to_parquet(curated_dir / "order_items_curated.parquet", index=False)
    print("ok")

    print("[build_curated] orders -> delivery_delay_days, order_total_value ...", end=" ", flush=True)
    orders_curated = curate_orders(pd.read_parquet(staging_dir / "orders.parquet"), order_items_curated)
    orders_curated.to_parquet(curated_dir / "orders_curated.parquet", index=False)
    print("ok")

    return {"order_items_curated": len(order_items_curated), "orders_curated": len(orders_curated)}


if __name__ == "__main__":
    run()
