# 5. Configuration

> [Index](README.md) · Previous: [4. Code reference](04-code-reference.md) · Next: [6. Running and testing](06-running-and-testing.md)

Pipeline behavior comes from four YAML files and one environment variable. This chapter documents every key and shows how to make the most common changes.

| File | Question it answers | Read by |
|---|---|---|
| `config/settings.yaml` | Where do things live? | `src/config.py` → every module |
| `catalog/inventory.yaml` | Which tables exist, what they mean, who owns them, what is critical? | Ingestion, DAMA, lineage, severity, certification |
| `contracts/<table>.contract.yaml` | What must this table satisfy? | Validator, DAMA, volume pillar |
| `config/slo.yaml` | Which targets must each table meet? | `src/quality/sli_slo.py` |

## 5.1 `config/settings.yaml`

```yaml
environment: local

local:
  raw_path: "data/raw"
  staging_path: "data/staging"
  curated_path: "data/curated"
  ingestion_logs_path: "reports/ingestion_logs"
  contracts_path: "contracts"
  dq_reports_path: "reports/dq_scorecards"
  quality_history_path: "reports/quality_history"
  alerts_path: "reports/alerts"
  incidents_path: "reports/incidents"
  certification_path: "reports/certification"
  dashboard_path: "reports/maturity_dashboard"

catalog_path: "catalog/inventory.yaml"
slo_path: "config/slo.yaml"
```

| Key | Content |
|---|---|
| `environment` | Which block to use. A `prod` block with other paths would allow switching environments without touching code |
| `raw_path` | Original CSVs |
| `staging_path` / `curated_path` | Validated Parquet / analytical layer |
| `ingestion_logs_path` | JSON log of each ingestion |
| `dq_reports_path` | Contract reports, DAMA scorecards and pillar reports |
| `quality_history_path` | Snapshots per table (the observability baseline) |
| `alerts_path` / `incidents_path` | Alerts / incidents, resolutions, RCAs and postmortems |
| `certification_path` / `dashboard_path` | Certification status / maturity dashboards |
| `catalog_path` / `slo_path` | Outside the environment block: they are definitions, not data |

Every path is relative to the project root (`PROJECT_ROOT`).

## 5.2 `catalog/inventory.yaml`

The metadata inventory. Example of an entry:

```yaml
version: 1
domain: e-commerce
owner_default: "data-eng-team"

tables:
  order_items:
    file: olist_order_items_dataset.csv
    description: >
      Items for each order, with price and freight per item.
    primary_key: [order_id, order_item_id]
    foreign_keys:
      - column: order_id
        references: orders.order_id
      - column: product_id
        references: products.product_id
    criticality: high
    owner: null
    known_gotchas: >
      price and freight_value are the fields used to calculate revenue...
```

| Key | Used by | Effect |
|---|---|---|
| `owner_default` | Certification | Owner inherited by tables with `owner: null` |
| `tables.<name>` | All | Entry order is processing order |
| `file` | Ingestion, simulator | CSV name in `raw_path` |
| `description` | People | Documentation |
| `primary_key` | DAMA (uniqueness) | `str`, `list` (composite) or `null` (no PK, like `geolocation`) |
| `foreign_keys` | DAMA (consistency), table-level lineage | Each item `{column, references: "table.column"}` |
| `criticality` | Alert severity | `high` → critical, `medium` → warning, `low` → info |
| `owner` | Certification | Specific owner; `null` inherits `owner_default` |
| `known_gotchas` | People | Behaviors that look like errors but are expected |

**Current criticality:** `orders`, `order_items` and `order_payments` are `high`; `customers`, `order_reviews` and `products` are `medium`; `sellers`, `geolocation` and `category_translation` are `low`.

**Documented gotchas:**

- `orders`: null delivery dates for canceled orders.
- `customers`: `customer_id` ≠ customer (it is one id per order); 278 ZIP prefixes without coordinates.
- `order_reviews`: `review_id` repeats in 814 rows; the PK is `(review_id, order_id)`.
- `products`: the columns `product_name_lenght` and `product_description_lenght` are misspelled at the source and were kept.
- `geolocation`: the same ZIP prefix appears in several rows; there is no PK.

## 5.3 `contracts/<table>.contract.yaml`

```yaml
table: order_items                      # must equal the catalog key
owner: data-eng-team
version: 1
primary_key: [order_id, order_item_id]  # optional; single or composite

columns:
  order_item_id:
    type: integer
    nullable: false
  price:
    type: float
    nullable: false
    min_value: 0
    max_value: 10000
  # other options: unique: true, allowed_values: [...]

sla:
  min_expected_volume: 100000
```

| Key | Values | Rule produced | Severity |
|---|---|---|---|
| `columns.<col>` | — | missing column / extra column | blocking / warning |
| `type` | `string`, `integer`, `float`, `timestamp` | `type_mismatch` | blocking |
| `nullable` | `true` (default) / `false` | `unexpected_nulls` | blocking |
| `unique` | `true` / `false` (default) | `uniqueness_violation` | blocking |
| `allowed_values` | list | `unexpected_value` | blocking |
| `min_value` / `max_value` | number (inclusive) | `below_min_value` / `above_max_value` | blocking |
| `primary_key` | `str` or `list` | `primary_key_nulls` / `primary_key_violation` | blocking |
| `sla.min_expected_volume` | integer | `volume_below_expected` | blocking |

**Current minimum volumes** (actual rows in parentheses): orders 90,000 (99,441), customers 90,000 (99,441), order_items 100,000 (112,650), order_payments 95,000 (103,886), order_reviews 90,000 (99,224), products 30,000 (32,951), sellers 3,000 (3,095), geolocation 900,000 (1,000,163), category_translation 60 (71).

## 5.4 `config/slo.yaml`

```yaml
slos:
  - id: orders_volume_stability
    table: orders
    pillar: volume
    description: >
      The volume of 'orders' must not deviate by more than 3 standard deviations...
    z_threshold: 3.0
    method: moving
    window: 10
```

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | Unique identifier (tested) |
| `table` | yes | Catalog table (tested) |
| `pillar` | yes | `freshness`, `volume`, `schema` or `distribution` (`load_slos` rejects anything else) |
| `description` | yes | The SLA in words |
| `max_delay_hours` | freshness | Maximum delay |
| `z_threshold` | volume, distribution | \|z\| threshold |
| `method` | volume, distribution | `zscore`, `moving` or `ewma` |
| `window` | volume, distribution | Runs in the baseline |

A test ensures each SLO uses only parameters its pillar understands. (table, pillar) pairs **without** an SLO use `DEFAULT_PILLAR_PARAMS`: 24h freshness, and `z_threshold: 3.0, method: moving, window: 10` for the statistical pillars.

**Current SLOs:**

| id | Table | Pillar | Target |
|---|---|---|---|
| `orders_freshness` | orders | freshness | 24h |
| `orders_volume_stability` | orders | volume | \|z\| ≤ 3, moving, window 10 |
| `order_items_distribution_stability` | order_items | distribution | \|z\| ≤ 3, moving, window 10 |
| `order_reviews_schema_stability` | order_reviews | schema | no schema change |
| `products_freshness` | products | freshness | 48h |

## 5.5 Environment variable

| Variable | Effect |
|---|---|
| `ALERT_WEBHOOK_URL` | If set to an `https://` URL, each alert is also posted as `{"text": "..."}` (Slack webhook format). Other schemes are ignored with a warning |

`.env` is in `.gitignore`. It also holds the `KAGGLE_API_TOKEN` used to download the data.

## 5.6 Recipes

**Tighten a rule.** Edit the contract, for example `max_value: 5000` on `price`. The next ingestion applies it. Test: `poetry run pytest tests/test_contracts.py`.

**Create an SLO.** Add an item to `config/slo.yaml`. `tests/test_slo.py` validates a unique id, an existing table, a known pillar and compatible parameters.

**Declare a new relationship.** Add it under `foreign_keys` in the catalog. No code needed: DAMA consistency starts measuring it, and table-level lineage includes it in incident impact.

**Add a new table:**

1. Put the CSV in `data/raw/`.
2. Add the entry to `catalog/inventory.yaml`.
3. Create `contracts/<table>.contract.yaml` with `table:` equal to the catalog key.
4. Run `poetry run pytest tests/test_contracts.py`: it fails if catalog and contracts are out of sync.

**Create a derived field in the curated layer:**

1. Implement it in a `curate_*` function in `src/transform/build_curated.py`.
2. Register it in `FIELD_LINEAGE_REGISTRY`.
3. If you forget step 2, `tests/test_curated.py` fails.
