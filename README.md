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
| 3. Observability pillars, SLOs, alerts and incidents | Done |
| 4. Curated layer, field-level lineage, certification, root cause analysis and maturity dashboard | Done |

## Documentation

The full study material - theory from the book applied to the code, architecture decisions, a reference of every module, configuration, and how to run, test and simulate incidents - lives in [`docs/`](docs/README.md), in **English** and **Portuguese**:

- [English documentation](docs/en/README.md)
- [Documentação em português](docs/pt-BR/README.md)

## Project Structure

```
catalog/inventory.yaml        # metadata inventory: files, primary/foreign keys, criticality, owner, known gotchas
config/settings.yaml          # paths per environment
config/slo.yaml               # Service Level Objectives per table and pillar
contracts/*.contract.yaml     # one data contract per table
data/raw/                     # original Olist CSVs (not versioned)
data/staging/                 # validated and typed Parquet files (not versioned)
data/curated/                 # analytical tables with derived fields (not versioned)
reports/ingestion_logs/       # JSON log per execution (not versioned)
reports/dq_scorecards/        # contract reports and DAMA scorecards per execution (not versioned)
reports/quality_history/      # metric snapshots per table, the baseline for observability (not versioned)
reports/alerts/               # alerts.jsonl (not versioned)
reports/incidents/            # incidents, resolutions, RCAs and postmortems (not versioned)
reports/certification/       # latest certification status per table (not versioned)
reports/maturity_dashboard/  # maturity KPIs per execution (not versioned)
orchestration/                # local end-to-end pipeline
scripts/simulate_incident.py  # incident simulator for study
docs/                         # full documentation (en / pt-BR)
src/config.py                 # shared settings and catalog loaders
src/ingestion/                # ingestion pipeline, contract validator and type casting
src/quality/                  # DAMA scorecard, observability pillars, SLO evaluation and checks runner
src/monitoring/               # metric history store, anomaly detection and alerting
src/incidents/                # incident log (TTD / TTR), RCA, postmortem and the incident CLI
src/transform/                # curated layer transformations
src/lineage/                  # field-level lineage registry
src/certification/            # table certification
src/reporting/                # maturity dashboard
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

4. **Run the observability checks** (pillars, SLOs, alerts and incidents):

```bash
poetry run python -m src.quality.checks_runner
```

Or run the whole pipeline at once - ingestion, DAMA, checks, certification, curated layer and dashboard:

```bash
poetry run python -m orchestration.local_pipeline
```

The orchestrator exits with code `1` when a table is blocked at ingestion or an SLO is breached. An optional webhook (e.g. Slack) receives the alerts when the `ALERT_WEBHOOK_URL` environment variable holds an `https://` URL.

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

## Stage 3 - Observability Pillars, SLOs and Incidents

The contract and the scorecard look at **one execution**. The pillars compare the current execution with the **history** of snapshots written by the ingestion, catching what no fixed rule anticipated:

| Pillar | Question | How |
|---|---|---|
| Freshness | Is the table up to date? | Hours since the last successful ingestion vs `max_delay_hours` |
| Volume | Did the row count change abruptly? | Z-score of `n_rows` against recent executions |
| Schema | Did columns or types change? | Columns and dtypes vs the previous execution |
| Distribution | Did the values shift? | Z-score of each numeric mean and null rate |
| Lineage | Who is affected? | Dependency graph built from the catalog `foreign_keys` |

The statistical pillars default to a **moving baseline** (last 10 executions, outliers pruned with median/MAD) instead of a global Z-score: an old incident left in the history would otherwise inflate the standard deviation and hide new problems. Below 3 previous executions there is not enough evidence, and the pillar reports `insufficient_history` - never a failure.

`config/slo.yaml` sets the targets (SLOs) per table and pillar; pairs without an SLO use the defaults in `src/quality/sli_slo.py`. Each (table, pillar) is evaluated **once**, so the SLO verdict, the alert and the incident always agree.

For every anomaly the checks runner:

1. sends an alert whose severity follows the table `criticality` in the catalog (`high` = critical, `medium` = warning, `low` = info);
2. registers an incident with the downstream tables impacted via lineage and the TTD (Time to Detection) - measured from the ingestion that brought the anomalous data;
3. skips anomalies already registered for the same snapshot, so re-running the checks on unchanged data opens no duplicate incidents.

A table blocked by its contract keeps its last snapshot, so it grows stale until the freshness SLO fires - the block becomes visible to everyone downstream.

## Stage 4 - Curated Layer, Certification and Incident Response

### Curated layer and field-level lineage

`src/transform/build_curated.py` builds `data/curated/` from staging, and only when its source tables passed their contract in the same execution - curated is never rebuilt from stale data.

| Table | Derived field | Transformation |
|---|---|---|
| `order_items_curated` | `item_total_value` | `price + freight_value` (one item, **not** the order) |
| `orders_curated` | `order_total_value` | `SUM(item_total_value) GROUP BY order_id` (null for orders without items) |
| `orders_curated` | `delivery_delay_days` | delivered - estimated, in days (positive = late) |

Each derived field is declared in `src/lineage/field_lineage.py`, so any field can be traced back to its source columns, across several levels:

```python
>>> trace_to_source_fields("orders_curated", "order_total_value")
[('order_items', 'price'), ('order_items', 'freight_value')]
```

A test fails if a curated column is created without its lineage entry, so the registry cannot drift from the code.

### Certification

A table is **certified** when it has an owner, enough history for every pillar to give a verdict, no pillar in anomaly and no open incident. The verdicts are the same ones used by the SLOs, and the status is written to `reports/certification/certification_status.yaml`.

### Incident response

Detection is automatic; investigation and resolution are human. The incident CLI closes the loop:

```bash
poetry run python -m src.incidents.cli list                       # open incidents
poetry run python -m src.incidents.cli rca 662dd7b2 --why "..." --why "..." --root-cause "..." --by "name"
poetry run python -m src.incidents.cli postmortem 662dd7b2 --impact "..." --timeline "..." --action "..."
poetry run python -m src.incidents.cli resolve 662dd7b2 --summary "what fixed it"
```

Ids accept any unique prefix. The RCA follows the 5 Whys and the postmortem is **blameless** by design: it records who investigated, never who caused the problem. A resolved incident releases the table for certification again.

### Maturity dashboard

`reports/maturity_dashboard/` gathers the reliability KPIs of each execution: incidents (open, resolved, by severity), mean TTD, mean TTR, % of SLOs met and % of certified tables.

## Simulating Incidents

`scripts/simulate_incident.py` breaks one raw CSV at a time to exercise each defense (contract, volume, distribution, null rate, schema), backing up `data/raw/` first:

```bash
poetry run python -m scripts.simulate_incident list
poetry run python -m scripts.simulate_incident volume_drop
poetry run python -m orchestration.local_pipeline
poetry run python -m scripts.simulate_incident restore
```

Run the pipeline a few times before simulating: the statistical pillars need at least 3 previous executions. The expected result of each scenario is described in [docs/en/06-running-and-testing.md](docs/en/06-running-and-testing.md#65-simulating-incidents).

## Tests and Lint

```bash
poetry run pytest
poetry run ruff check .
```
