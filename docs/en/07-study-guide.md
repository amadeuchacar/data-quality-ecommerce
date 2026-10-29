# 7. Study guide

> [Index](README.md) · Previous: [6. Running and testing](06-running-and-testing.md)

## 7.1 Code reading path

An order that follows the data and goes from simplest to most composed:

1. `src/config.py`: where the paths come from.
2. `contracts/orders.contract.yaml` and `catalog/inventory.yaml`: the rules and the metadata.
3. `src/ingestion/type_casting.py` → `contract_validator.py` → `load_raw.py`: Stage 1.
4. `src/monitoring/history_store.py`: what is stored on every run.
5. `src/quality/dimensions_dama.py`: Stage 2.
6. `src/monitoring/anomaly_detection.py` → `src/quality/pillars/` → `sli_slo.py` → `checks_runner.py`: Stage 3.
7. `src/incidents/` → `src/certification/` → `src/transform/` + `src/lineage/` → `src/reporting/`: Stage 4.
8. `orchestration/local_pipeline.py`: everything together.

For each module, read the matching test in `tests/`. The tests are the executable specification of the behavior.

## 7.2 Glossary

| Term | Definition |
|---|---|
| **Data downtime** | Period when data is partial, erroneous, missing or inaccurate; estimated as number of incidents × (TTD + TTR) |
| **Data contract** | Declared agreement on the schema, rules and volume a table must satisfy |
| **Circuit breaker** | Mechanism that stops bad data before it propagates |
| **Staging** | Layer with validated, typed data |
| **Curated** | Analytical layer, with documented derived fields |
| **Snapshot** | Picture of a table's metrics in one run (rows, columns, types, nulls, means) |
| **Pillar** | One observability dimension: freshness, volume, schema, distribution, lineage |
| **Z-score** | Distance from a value to the mean, in standard deviations |
| **MAD** | Median absolute deviation: a dispersion measure robust to outliers |
| **EWMA** | Exponentially weighted moving average: gives more weight to recent values |
| **Baseline** | The historical values the present is compared against |
| **SLA / SLI / SLO** | Agreement / measured indicator / numeric target |
| **TTD / TTR** | Time to detection / time to resolution |
| **RCA** | Root cause analysis (here, the 5 Whys technique) |
| **Blameless postmortem** | Incident record focused on learning, without assigning blame |
| **Table / field lineage** | Dependency between tables / between columns |
| **Downstream / upstream** | Consumers / sources |
| **Certification** | "Safe to use" seal, granted with evidence and no open incident |
| **DAMA dimensions** | Completeness, uniqueness, validity, consistency, timeliness, accuracy |
| **`insufficient_history`** | "There is not enough evidence to state anything" |

## 7.3 Known limitations

Conscious limitations, with the solution a next step could adopt:

| Limitation | Consequence | Possible solution |
|---|---|---|
| **Schema compared only with the previous run** | Rolling back a change also counts as a change and opens a second incident (observed in `schema_drift`) | Also compare with the contract schema, treating a return to the expected schema as recovery |
| **Freshness measures the load, not the data** | The dataset is static (2016–2018). "Fresh" means "recently loaded", not "contains recent orders" | Also measure `max(order_purchase_timestamp)` |
| **Table-level lineage via foreign keys** | Overstates impact: almost everything reaches `orders` | Lineage extracted from jobs and queries |
| **SLO per run** | A real SLO measures a window ("99% of the month's runs") | Compute over `alerts.jsonl` and the history |
| **Never-loaded table = `insufficient_history`** | A table that never reached staging does not trigger freshness | Treat "no history" as a freshness anomaly for catalog tables |
| **Curated layer is not monitored** | Pillars look only at staging | Snapshots of curated tables too |
| **DAMA percentages are not SLOs** | 99.72% consistency is informational only | A `dama` pillar with targets in `slo.yaml` |
| **ZIP code read as integer** | Leading zeros are lost (01001 becomes 1001) | Declare the ZIP code as `string` and fix the dtype at read time |
| **Data downtime is not computed** | The dashboard has the three factors, not the product | `n_incidents × (ttd_mean + ttr_mean)` in the dashboard |
| **pandas, JSONL and a single process** | Does not scale or allow concurrency | See [3.5](03-architecture.md#35-accepted-costs-and-production-alternatives) |

## 7.4 Review questions

Short answers are in parentheses.

**Concepts**

1. Why can a pipeline without errors cause data downtime? (The job measures whether it *ran*, not whether the data is *right*.)
2. What are the three levers in the data downtime formula, and which stage works on each? (Fewer incidents: contract. TTD: pillars. TTR: incident process.)
3. Why is the contract not enough? (It only catches what someone foresaw; the pillars catch the unforeseen.)
4. What is the difference between SLA, SLI and SLO? (Agreement, measured indicator, numeric target.)
5. Why is the postmortem blameless? (So people report problems instead of hiding them; the focus is why the system allowed the failure.)

**Architecture**

6. If `products` violates its contract, what happens to `sellers`? (Nothing: the circuit breaker is per table.)
7. Why is no snapshot written when the contract fails? (To avoid contaminating the baseline, and so the table ages and freshness exposes it.)
8. Why are validity and uniqueness 100% on staging? (The contract already blocked anything that would break them; on staging they certify the gate.)
9. Why is each pillar evaluated only once per run? (So the alert, the incident, the SLO, the certification and the dashboard never disagree.)
10. Why does `insufficient_history` pass the SLO but prevent certification? (Failing without data would raise false alarms; certifying without data would grant trust with no basis.)
11. Why doesn't severity use the worst criticality among impacted tables? (With FKs as lineage, almost everything reaches `orders`, and every alert would become critical.)
12. Why is the curated layer rebuilt only if its sources passed in this run? (To avoid mixing new data from one table with old data from another.)

**Code**

13. How does the validator find out that a value is not of the declared type? (It converts with `errors="coerce"` and counts nulls that did not exist before.)
14. Why `.fillna(False)` on the range masks? (Comparisons with `Int64` return `<NA>` where the value is null.)
15. Why does outlier pruning use median and MAD instead of mean and standard deviation? (They are robust: the outlier itself barely moves them.)
16. Why did an 8% drop in `orders` give exactly z = −8? (Constant history → the deviation floor is 1% of the mean → 8% / 1% = 8.)
17. How does the system avoid opening the same incident on every run? (The (table, pillar, snapshot) key in `find_incident`.)
18. How is TTD estimated? (Data pillars: from the snapshot that brought the data; freshness: from the moment the delay crossed the SLO.)
19. What keeps the field-level lineage registry from going stale? (The test that compares the columns created by the code with those declared in the registry.)
20. Why was `total_order_value` renamed? (The value was per item, and 9,803 orders have several items; whoever summed the column would get revenue wrong.)

## 7.5 Suggested exercises

1. Apply `null_spike` and confirm in the `pillars_report` which column fired and with which Z.
2. Create a distribution SLO for `products` and repeat exercise 1: now the exit code should be 1.
3. Read `test_moving_detects_anomaly_masked_by_contaminated_history` in `tests/test_anomaly_detection.py` and compute by hand the mean and deviation of the history with and without the outlier. Explain why the global Z-score cannot see the value 500.
4. Add to the catalog an FK pointing to a column that does not exist (for example, `sellers.seller_region → geolocation.region`) and observe the `-` (not applicable) result in DAMA consistency. Then remove the FK.
5. Implement the data downtime calculation in the dashboard and write the matching test.
