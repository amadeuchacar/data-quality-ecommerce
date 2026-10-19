# Brazilian E-Commerce by Olist - Data Quality

Data engineering study project applying the concepts from the book **Data Quality Fundamentals** (Barr Moses, Lior Gavish, Molly Vorwerck - O'Reilly) to the [Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce).

## Motivation

A pipeline can run successfully and still produce incorrect data — what the book refers to as **data downtime**.

This project puts into practice the mechanisms proposed to reduce this risk: data contracts, the observability pillars (freshness, volume, schema, distribution, lineage), SLAs/SLIs/SLOs, incident management, and data certification.

## Project Stages

| Stage | Status |
|---|---|
| 1. Ingestion with data contracts (raw -> staging) | Done |
| 2. Quality dimensions scorecard (DAMA UK) on staging | Done |
| 3. Observability pillars (freshness, volume, schema, distribution) | Planned |
| 4. Lineage, SLAs/SLIs/SLOs, incident management and certification | Planned |

## Project Structure

```
catalog/inventory.yaml        # metadata inventory: files, primary/foreign keys, criticality, owner, known gotchas
config/settings.yaml          # paths per environment
contracts/*.contract.yaml     # one data contract per table
data/raw/                     # original Olist CSVs (not versioned)
data/staging/                 # validated and typed Parquet files (not versioned)
reports/ingestion_logs/       # JSON log per execution (not versioned)
reports/dq_scorecards/        # contract reports and DAMA scorecards per execution (not versioned)
reports/quality_history/      # metric snapshots per table, the baseline for observability (not versioned)
src/config.py                 # shared settings and catalog loaders
src/ingestion/                # ingestion pipeline, contract validator and type casting
src/quality/                  # DAMA UK quality dimensions scorecard
src/monitoring/               # metric history store
tests/                        # unit tests
```

## How to Run

The project uses **Poetry** and is version-controlled with Git/GitHub.

```bash
poetry install
```

1. **Download the data** - see [`scripts/download_instructions.md`](scripts/download_instructions.md).
2. **Run the ingestion**:

```bash
poetry run python -m src.ingestion.load_raw
```

The process exits with code `0` when every table reaches staging and `1` when at least one table is blocked.

3. **Generate the DAMA quality scorecard** (reads the staging layer, so run it after the ingestion):

```bash
poetry run python -m src.quality.dimensions_dama
```

## Stage 1 - Ingestion with Data Contracts

Each table listed in `catalog/inventory.yaml` goes through the same flow:

1. The CSV is read from `data/raw/`.
2. The data is validated against its contract in `contracts/<table_name>.contract.yaml`.
3. If any **blocking** rule is violated, the table is stopped right there (**circuit breaker**) and does not reach staging. The remaining tables continue normally.
4. If the contract passes, the data is cast to the contract types and written to `data/staging/<table_name>.parquet`.
5. A metric snapshot (row count, null rates, mean/std of numeric columns) is appended to `reports/quality_history/<table_name>.jsonl`. This history is the baseline the observability stage will use to detect anomalies with Z-Score instead of hardcoded thresholds.

Every execution also writes a JSON log to `reports/ingestion_logs/` and a human-readable scorecard to `reports/dq_scorecards/`.

### Contract Rules

| Rule | Contract key | Severity |
|---|---|---|
| Column expected by the contract is missing | `columns` | blocking |
| Column not declared in the contract (schema drift) | `columns` | warning |
| Value cannot be cast to the declared type (`string`, `integer`, `float`, `timestamp`) | `type` | blocking |
| Null values in a non-nullable column | `nullable: false` | blocking |
| Duplicated values in a unique column | `unique: true` | blocking |
| Null or duplicated primary key (single or composite) | `primary_key` | blocking |
| Value outside the accepted list | `allowed_values` | blocking |
| Value outside the business range | `min_value` / `max_value` | blocking |
| Fewer rows than expected | `sla.min_expected_volume` | blocking |

Example (`contracts/order_items.contract.yaml`):

```yaml
table: order_items
owner: data-eng-team
version: 1
primary_key: [order_id, order_item_id]

columns:
  price:
    type: float
    nullable: false
    min_value: 0
    max_value: 10000

sla:
  min_expected_volume: 100000
```

### Known Gotchas

Some behaviors of the dataset are expected and documented in the catalog so they are not treated as quality errors:

- `orders`: delivery dates can be null for canceled or undelivered orders.
- `order_reviews`: `review_id` alone is not unique - the primary key is `(review_id, order_id)`.
- `geolocation`: the same ZIP code prefix has multiple rows, so there is no primary key.
- `products`: the columns `product_name_lenght` and `product_description_lenght` are misspelled in the source file and are kept as they are.

## Stage 2 - Quality Dimensions Scorecard (DAMA UK)

The contract answers **"pass or fail?"**. The scorecard answers **"how good, in percent?"**, measuring 4 of the 6 [DAMA UK](https://www.dama-uk.org/) data quality dimensions on every staging table:

| Dimension | What it measures | Source of the rule |
|---|---|---|
| Completeness | % of non-null values per column | contract (`nullable`) |
| Uniqueness | % of rows not duplicated on the primary key | catalog (`primary_key`) |
| Validity | % of values respecting type, accepted values and ranges | contract (same rules as the validator) |
| Consistency | % of foreign key values found in the parent table | catalog (`foreign_keys`) |

Timeliness and Accuracy are not covered yet: the first belongs to the freshness pillar (Stage 3), the second needs a trusted reference source the dataset does not provide.

Since only tables that passed their contract reach staging, **Validity and Uniqueness work as a certification of the gate** and are expected to be 100%. The gradual signal comes from:

- **Consistency** - a cross-table check that the per-table contract cannot make. It currently finds orphan values in `customers` and `sellers` (ZIP code prefixes without coordinates in `geolocation`) and in `products` (categories without an English translation).
- **Completeness of optional columns** - reported per column (marked as *optional*) but left out of the table summary, because nulls there are expected.

Each execution writes `dama_scorecard_<timestamp>.json` and `.md` to `reports/dq_scorecards/`. The Markdown starts with a summary table (the worst column or relation of each table per dimension) followed by the details per column. These percentages are natural SLIs for the SLO stage.

## Tests and Lint

```bash
poetry run pytest
poetry run ruff check .
```
