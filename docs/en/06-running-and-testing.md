# 6. Running and testing

> [Index](README.md) · Previous: [5. Configuration](05-configuration.md) · Next: [7. Study guide](07-study-guide.md)

## 6.1 Prerequisites and installation

- Python 3.11 or later
- [Poetry](https://python-poetry.org/) 2.x
- The 9 Olist CSVs in `data/raw/`

```bash
poetry install
```

The project uses `package-mode = false`: Poetry installs the dependencies but does not package the project. Runtime dependencies: pandas, pyarrow and pyyaml. Development dependencies: pytest, ruff and kaggle.

**Downloading the data** (details in `scripts/download_instructions.md`):

```bash
# authenticate first: poetry run kaggle auth login (or a KAGGLE_API_TOKEN; .env is not read)
poetry run kaggle datasets download -d olistbr/brazilian-ecommerce -p data/raw --unzip
```

## 6.2 Running

### Everything at once

```bash
poetry run python -m orchestration.local_pipeline
```

Condensed output of a clean run:

```
STEP 1/6 - Ingestion + Contract Validation
[ingestion] completed: 9/9 tables processed successfully.
STEP 2/6 - DAMA Quality Dimensions (staging)
STEP 3/6 - Observability Pillars + SLOs
[checks_runner] SLOs: 5/5 meeting objectives.
STEP 4/6 - Certification
[certify] 9/9 tables certified.
STEP 5/6 - Curated Layer + Field Lineage
[build_curated] order_items -> item_total_value ... ok
[build_curated] orders -> delivery_delay_days, order_total_value ... ok
STEP 6/6 - Maturity Dashboard
[maturity_dashboard] SLOs met: 5/5 | certified tables: 9/9
SUMMARY
Tables blocked at ingestion: none
SLOs breached: none
Certified tables: 9/9
```

> **First runs:** the volume and distribution pillars need 3 previous runs. Until then they answer `insufficient_history`, and certification is denied for lack of evidence. This is expected (D9). Run the ingestion a few times to build the baseline.

### Step by step

| Command | What it does | Exit code |
|---|---|---|
| `poetry run python -m src.ingestion.load_raw` | Ingestion + contract + snapshot | 1 if some table is blocked |
| `poetry run python -m src.quality.dimensions_dama` | DAMA scorecard on staging | always 0 |
| `poetry run python -m src.quality.checks_runner` | Pillars, SLOs, alerts and incidents | 1 if some SLO is breached |
| `poetry run python -m src.certification.certify` | Certification | always 0 |
| `poetry run python -m src.transform.build_curated` | Curated layer | always 0 |
| `poetry run python -m src.reporting.maturity_dashboard` | Dashboard | always 0 |
| `poetry run python -m src.incidents.cli <subcommand>` | Human incident process | 1 on usage error |

Always use `-m` from the project root: imports look like `from src.ingestion...`.

## 6.3 Where the outputs go

| File | Produced by | Content |
|---|---|---|
| `data/staging/<table>.parquet` | ingestion | Approved, typed data |
| `data/curated/*_curated.parquet` | curated layer | Tables with derived fields |
| `reports/ingestion_logs/run_<ts>.json` | ingestion | Full technical log, per table |
| `reports/dq_scorecards/contract_report_<ts>.md` | ingestion | Contract summary and violations |
| `reports/dq_scorecards/dama_scorecard_<ts>.json` / `.md` | DAMA | Dimensions per table and column |
| `reports/dq_scorecards/pillars_report_<ts>.md` | runner | SLOs and pillars per table |
| `reports/quality_history/<table>.jsonl` | ingestion | One snapshot per approved run |
| `reports/alerts/alerts.jsonl` | runner | One alert per line |
| `reports/incidents/incidents.jsonl` | runner | Incidents, with TTD |
| `reports/incidents/resolutions.jsonl` | CLI | Resolutions, with TTR |
| `reports/incidents/rca.jsonl` | CLI | Root cause analyses |
| `reports/incidents/postmortems/<id>.md` | CLI | Postmortems |
| `reports/certification/certification_status.yaml` | certification | Latest status per table |
| `reports/maturity_dashboard/dashboard_<ts>.md` | dashboard | Maturity KPIs |

## 6.4 Automated tests and lint

```bash
poetry run pytest                                    # 172 tests pass; 6 are skipped on purpose
poetry run pytest tests/test_pillars.py              # one file
poetry run pytest -k freshness                       # tests whose name contains "freshness"
poetry run pytest -v                                 # name of each test
poetry run ruff check .                              # lint
```

The 6 skipped tests are for tables without a `primary_key` declared in the contract. The tests **do not read** `data/raw/`: they run in under 1 second and work without downloading the dataset. Each test file is described in [4.21](04-code-reference.md#421-tests).

## 6.5 Simulating incidents

The simulator (`scripts/simulate_incident.py`) changes one raw CSV at a time to exercise a specific defense. It copies `data/raw/` to `data/raw_backup/` before the first change.

### Walkthrough

```bash
# 0. Baseline: at least 4 clean runs
poetry run python -m src.ingestion.load_raw          # repeat 3x
poetry run python -m orchestration.local_pipeline    # should end with 9/9 certified

# 1. See the scenarios
poetry run python -m scripts.simulate_incident list

# 2. Apply a scenario and run the pipeline
poetry run python -m scripts.simulate_incident volume_drop
poetry run python -m orchestration.local_pipeline

# 3. Restore the original data and run again
poetry run python -m scripts.simulate_incident restore
poetry run python -m orchestration.local_pipeline

# 4. Close the incident (see 6.6)
```

> **Caution:** simulations leave marks in your history (`reports/`). To study without affecting your current state, back up `reports/` first or use a copy of the project. To start from scratch, see 6.9.

### Observed results

The results below were obtained on a copy of the project, with 4 clean baseline runs and the scenarios applied in sequence.

**`contract_violation`** (10 negative prices in `order_items`)

```
[ingestion] Processing 'order_items' ... contract_violation
[ingestion] completed: 8/9 tables processed successfully.
[curated] SKIPPED: source tables blocked or missing in this ingestion: ['order_items'].
Tables blocked at ingestion: ['order_items']
Certified tables: 9/9
EXIT=1
```

What to notice:

- The circuit breaker blocked only `order_items`; the other 8 tables went through.
- The curated layer was **not** rebuilt, because one of its sources did not pass in this run.
- Certification stayed at 9/9: the last good snapshot of `order_items` is still within the freshness SLO (24h). If the problem persists for more than 24h, the freshness pillar exposes the table.

**`volume_drop`** (last 8% of `orders` removed)

```
[ALERT-CRITICAL] [VOLUME] orders: 91485 rows (Z-score=-8.00, baseline[moving]: mean=99441, std=994).
  | downstream impact: order_items, order_payments, order_reviews
  [FAILED] orders_volume_stability: 91485 rows (Z-score=-8.00, ...)
[certify] orders: not certified
SLOs breached: ['orders_volume_stability']
EXIT=1
```

What to notice:

- 91,485 rows **pass** the contract (minimum 90,000). Only statistics over the history caught the drop: defense in depth.
- The deviation of 994 is the 1% floor of the mean (the history was constant). That is why an 8% drop gives exactly z = −8.
- Severity is `critical` because `orders` has criticality `high`.
- **After `restore`:** the SLO passes again (MAD pruning discarded the anomalous point from the baseline), but `orders` **stays uncertified** until the incident is resolved.

**`distribution_shift`** (`freight_value × 3`)

```
[ALERT-CRITICAL] [DISTRIBUTION] order_items: freight_value: current mean=59.97 (Z=200.00) | downstream impact: none
  [FAILED] order_items_distribution_stability: freight_value: current mean=59.97 (Z=200.00)
```

The contract does not catch the change, because `freight_value` only has `min_value: 0`. Distribution does.

**`null_spike`** (30% of `product_category_name` nulled)

```
[ALERT-WARNING] [DISTRIBUTION] products: product_category_name: nulls=31.29% (Z=1590.36) | downstream impact: order_items
SLOs breached: none
EXIT=0
```

What to notice:

- The column is `nullable: true`, so the contract accepts the nulls.
- There was an incident and certification was lost, but **no SLO was breached** and the exit code is 0, because `products` only has a freshness SLO. Incident and SLO are different mechanisms: one says "something changed", the other says "a commitment was broken".
- Severity is `warning` (`products` has criticality `medium`).

**`schema_drift`** (new column `review_channel` in `order_reviews`)

```
- **[warning]** `unexpected_column` - column `review_channel`: ... (potential schema drift).
[ALERT-WARNING] [SCHEMA] order_reviews: added columns: ['review_channel'] | downstream impact: none
  [FAILED] order_reviews_schema_stability: added columns: ['review_channel']
```

The contract only **warns** (a new column does not break consumers); the schema pillar records the change and breaks the SLO.

> **Observed limitation:** after `restore`, the schema pillar fires **again**, with "removed columns: ['review_channel']". It compares with the previous run, which was the broken one, so the rollback is also a change. See [7.3](07-study-guide.md#73-known-limitations).

### Freshness (manual)

Freshness depends on the clock. To simulate it without waiting 24h, temporarily lower the threshold in `config/slo.yaml` (`orders_freshness: max_delay_hours: 0.01`) and run only the pillars:

```bash
poetry run python -m src.quality.checks_runner
```

```
[ALERT-CRITICAL] [FRESHNESS] orders: Last successful ingestion was 0.1h ago (threshold: 0.01h).
  [FAILED] orders_freshness: Last successful ingestion was 0.1h ago (threshold: 0.01h).
```

The incident records a TTD of about 343s: the time since the delay crossed the threshold, not since the last load. **Set the value back to 24 afterwards.**

## 6.6 Closing an incident

```bash
poetry run python -m src.incidents.cli list
```

```
9147bf21  critical  orders/volume                 detected 2026-10-26T21:41:01  91485 rows (Z-score=-8.00, ...)
6b517382  critical  order_items/distribution      detected 2026-10-26T21:41:35  freight_value: current mean=59.97 (Z=200.00)
```

Ids accept any unique prefix:

```bash
poetry run python -m src.incidents.cli rca 9147bf21 \
  --why "The orders export ran before the upstream job finished" \
  --why "The export has no completion check" \
  --root-cause "Missing dependency between upstream job and export" \
  --by "Amadeu"

poetry run python -m src.incidents.cli postmortem 9147bf21 \
  --impact "Revenue dashboards understated by 8% for one run" \
  --timeline "Partial export ingested" \
  --timeline "Volume pillar raised a critical alert" \
  --action "Add a completion flag check before export"

poetry run python -m src.incidents.cli resolve 9147bf21 --summary "Export re-run after upstream completion"
```

```
RCA recorded for 9147bf21-... (2 whys).
Postmortem saved to: reports\incidents\postmortems\9147bf21-....md
Resolved 9147bf21-... - TTR 0.01h.
```

On the next run, `orders` is **CERTIFIED** again and the dashboard shows the mean TTR:

```
[certify] orders: CERTIFIED
[maturity_dashboard] incidents: 5 (4 open) | mean TTD: 5.7s | mean TTR: 53.2s
```

The generated postmortem has these sections: summary (table, pillar, detection, TTD, impacted tables), timeline, impact, the 5 Whys, root cause, corrective actions and investigation metadata. It has no culprit field.

## 6.7 Testing the webhook

```bash
# PowerShell
$env:ALERT_WEBHOOK_URL = "https://hooks.slack.com/services/..."
poetry run python -m src.quality.checks_runner
```

A URL that does not start with `https://` is ignored with a warning, and a network failure never stops the pipeline.

## 6.8 Inspecting the results

```python
import pandas as pd
pd.read_parquet("data/staging/orders.parquet").dtypes      # contract types
pd.read_parquet("data/curated/orders_curated.parquet")[["order_id", "order_total_value", "delivery_delay_days"]]

from src.lineage.field_lineage import describe_lineage
describe_lineage("orders_curated", "order_total_value")
```

## 6.9 Starting from scratch

Everything in `data/staging/`, `data/curated/` and `reports/` is generated and can be deleted. `data/raw/` should not be deleted. To delete only the observability and incident state:

```bash
# Git Bash
rm -rf reports/quality_history/*.jsonl reports/alerts reports/incidents reports/certification
```

After that, 4 runs are needed again to build the baseline.

## 6.10 Common problems

| Symptom | Cause | Fix |
|---|---|---|
| Every statistical pillar in `insufficient_history` and nothing certified | Fewer than 4 runs in the history | Run the ingestion more times |
| `ModuleNotFoundError: No module named 'src'` | Run without `-m` or outside the root | `poetry run python -m ...` from the root |
| Table `missing_file` | CSV missing from `data/raw/` | Download the data; check `file` in the catalog |
| Freshness SLO breached for no apparent reason | No ingestion in the last 24h | Run the ingestion; this is the expected behavior |
| `restore` says "nothing to restore" | No scenario applied, or already restored | Nothing to do |
| `UnicodeEncodeError` on a print | Windows console in cp1252 with emoji | The project already avoids emoji in the console; do not add emoji to `print` |
