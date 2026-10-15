"""
Consistency tests between the metadata catalog (catalog/inventory.yaml) and the data
contracts (contracts/*.contract.yaml). They guard against a table being added to one
place and forgotten in the other.
"""

from pathlib import Path

import pytest
import yaml

from src.ingestion.contract_validator import load_contract
from src.ingestion.type_casting import TYPE_CASTERS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = PROJECT_ROOT / "contracts"

with open(PROJECT_ROOT / "catalog" / "inventory.yaml", encoding="utf-8") as f:
    CATALOG = yaml.safe_load(f)

TABLES = list(CATALOG["tables"])


def test_every_contract_file_is_in_the_catalog():
    contract_tables = {p.name.removesuffix(".contract.yaml") for p in CONTRACTS_DIR.glob("*.contract.yaml")}

    assert contract_tables == set(TABLES)


@pytest.mark.parametrize("table_name", TABLES)
def test_contract_matches_catalog(table_name):
    contract = load_contract(table_name, CONTRACTS_DIR)

    assert contract["table"] == table_name
    assert contract["columns"], "contract must declare at least one column"
    assert contract.get("sla", {}).get("min_expected_volume") is not None


@pytest.mark.parametrize("table_name", TABLES)
def test_contract_uses_known_types(table_name):
    contract = load_contract(table_name, CONTRACTS_DIR)

    unknown = {col: rules.get("type") for col, rules in contract["columns"].items()
               if rules.get("type") not in TYPE_CASTERS}
    assert unknown == {}


@pytest.mark.parametrize("table_name", TABLES)
def test_contract_primary_key_columns_are_declared(table_name):
    contract = load_contract(table_name, CONTRACTS_DIR)
    primary_key = contract.get("primary_key")
    if primary_key is None:
        pytest.skip("no table-level primary key declared")

    key_columns = [primary_key] if isinstance(primary_key, str) else primary_key
    assert set(key_columns) <= set(contract["columns"])
