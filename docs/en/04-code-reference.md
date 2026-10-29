# 4. Code reference

> [Index](README.md) · Previous: [3. Architecture](03-architecture.md) · Next: [5. Configuration](05-configuration.md)

Modules appear in data-flow order. For each one: **what it does**, **why it exists**, **functions** (parameters and return values) and **implementation decisions**.

Convention used in the tables: `df` = `pandas.DataFrame`; "pure" = a function that neither reads nor writes files.

---

## 4.1 `src/config.py`

**What it does.** Resolves the project root and loads `config/settings.yaml` and `catalog/inventory.yaml`.

**Why it exists.** Before it, five modules had their own copies of `load_settings`, `load_catalog` and `PROJECT_ROOT`, each with small differences (one resolved the catalog path internally, another expected the caller to resolve it).

| Element | Parameters | Returns | Behavior |
|---|---|---|---|
| `PROJECT_ROOT` | — | `Path` | `Path(__file__).resolve().parents[1]`: the repository root, computed from the file itself. Works from any working directory |
| `load_settings(path=...)` | settings path (default `config/settings.yaml`) | `dict` | Returns a **copy** of the active environment block (`environment: local`) and adds `catalog_path`, `slo_path` and `environment` |
| `load_catalog(catalog_path)` | **absolute** path | `dict` | Reads the catalog YAML |

**Decision.** `load_settings` returns `dict(settings[env])`, a copy, so a module that changes the dictionary does not affect another.

---

## 4.2 `src/ingestion/type_casting.py`

**What it does.** Converts columns to the types declared in the contract.

**Why it exists.** The same conversion serves two purposes: the validator uses it to **detect** values that do not fit the type, and ingestion uses it to **write** typed staging.

| Element | Returns | Behavior |
|---|---|---|
| `TYPE_CASTERS` | `dict[str, Callable]` | `string` → `astype("string")`; `integer` → `_to_integer`; `float` → `to_numeric(...).astype("float64")`; `timestamp` → `to_datetime(errors="coerce")` |
| `_to_integer(series)` | `Int64` series | `to_numeric(errors="coerce")`, then nulls out non-integral values (`numeric % 1 == 0`) |
| `cast_to_contract(df, contract)` | new `df` | Applies the caster of each declared column; columns without a known type stay as they are; the original is not modified |

**Decisions.**

- **`errors="coerce"`** turns whatever does not convert into null. That is what makes detecting invalid values possible: a null that did not exist before the conversion is a value that did not fit the type.
- **`_to_integer` rejects 1.5.** Without this, `to_numeric` would accept 1.5 as an "integer" and the contract would not notice.
- **`Int64` (capital I)** is pandas' *nullable* integer. NumPy's `int64` cannot hold nulls and would turn the column into float.

---

## 4.3 `src/ingestion/contract_validator.py`

**What it does.** Validates a DataFrame against the table contract and says whether it may proceed.

### Types

| Type | Fields | Use |
|---|---|---|
| `Severity` | `"blocking"` or `"warning"` | Only *blocking* fails the table |
| `Violation` | `rule`, `column`, `detail`, `severity` | One broken rule |
| `ContractValidationResult` | `table`, `passed`, `violations`; `as_dict()` | The table verdict, JSON-serializable |

### Functions

| Function | Parameters | Returns |
|---|---|---|
| `load_contract(table_name, contracts_dir)` | table name, contracts folder | `dict` from `<table>.contract.yaml` |
| `column_rule_violations(series, rules)` | column values, column rules | `dict[rule, boolean mask]`, only for declared rules |
| `validate_dataframe(df, contract)` | raw DataFrame, contract | `ContractValidationResult` |
| `_check_primary_key(df, primary_key)` | DataFrame, single (`str`) or composite (`list`) PK | list of `Violation` |

### `column_rule_violations`: the validity rule in one place

Returns one mask per rule, `True` where the value **violates** it:

| Key | Computation |
|---|---|
| `type_mismatch` | `coerced.isna() & series.notna()`: a new null after conversion |
| `unexpected_value` | `coerced.notna() & ~coerced.isin(allowed_values)` |
| `below_min_value` | `(numeric < min_value).fillna(False)` |
| `above_max_value` | `(numeric > max_value).fillna(False)` |

Details that matter:

- **A null is never invalid here.** Nulls are a matter of nullability (in the contract) and completeness (in DAMA).
- **`.fillna(False).astype(bool)`:** after the cast, an `Int64` column compared with a number returns `<NA>` where the value was null. Without the `fillna`, `mask.any()` could return `NA`, and `|` between masks would propagate the `NA`.
- **The validator counts the masks (`sum`) and DAMA averages them (`mean`).** The two modules never disagree about what is valid.

### `validate_dataframe`: order of checks

1. Contract columns missing from the DataFrame → `missing_column` (*blocking*).
2. DataFrame columns missing from the contract → `unexpected_column` (*warning*: schema drift).
3. For each declared and present column: type, nulls (`nullable: false`), uniqueness (`unique: true`), accepted values and range.
4. Table primary key (`primary_key`), single or composite: nulls and duplicates.
5. Minimum volume (`sla.min_expected_volume`).

`passed = True` only if no violation is *blocking*.

**Decisions.**

- **It does not stop at the first error:** it collects every violation, so the report shows the full picture.
- **Uniqueness with `duplicated(keep=False)`:** counts every row involved in the duplicate, including the first, because the goal is to list who is in conflict.
- **Composite PK at table level:** `order_items` uses `[order_id, order_item_id]`; `order_id` repeats on its own, only the pair must be unique. `order_reviews` uses `[review_id, order_id]` because `review_id` repeats in 814 rows of the real data.

---

## 4.4 `src/ingestion/load_raw.py`

**What it does.** Runs Stage 1: CSV → contract → typed Parquet → snapshot → reports.

| Function | Parameters | Returns |
|---|---|---|
| `ingest_table(table_name, table_meta, raw_dir, staging_dir, contracts_dir, history_dir)` | table, catalog entry, four folders | per-table result `dict` |
| `generate_markdown_report(log_payload, report_path)` | run log, destination | — (writes the file) |
| `run()` | — | list of results |
| `main()` | — | exit code: 0 if all `ok`, 1 otherwise |

**`ingest_table` flow** (stops at the first failing step):

1. Does the file exist? If not, `status: missing_file`.
2. Does `pd.read_csv` work? I/O, encoding or parsing errors (`OSError`, `ValueError`) give `status: read_error`.
3. Raw profile: rows, columns and dtypes as pandas read them.
4. Contract: a *blocking* violation → `status: contract_violation`, and the function returns (**circuit breaker**).
5. `cast_to_contract` → writes `data/staging/<table>.parquet`.
6. `build_snapshot` + `append_snapshot` → `reports/quality_history/<table>.jsonl`.

**Result dictionary:** `table`, `source_file`, `status`, `error`, `n_rows`, `n_columns`, `columns`, `dtypes`, `contract_passed`, `contract_violations` and, if approved, `staging_path`.

**Outputs of `run()`:**

- `reports/ingestion_logs/run_<timestamp>.json`: the full technical log.
- `reports/dq_scorecards/contract_report_<timestamp>.md`: summary table and list of violations.

**Decisions.**

- **`except (OSError, ValueError)` instead of `Exception`:** catches only expected read failures. `UnicodeDecodeError` and `pandas.errors.ParserError` are subclasses of `ValueError`.
- **The snapshot is computed on the typed DataFrame**, so the dtypes in the history are stable across runs (the schema pillar compares dtypes).
- **UTC timestamps**, with `:` replaced by `-` in file names (Windows does not accept `:`).

---

## 4.5 `src/monitoring/history_store.py`

**What it does.** Stores a picture of each table's metrics on every approved ingestion. It is the pillars' memory.

| Element | Detail |
|---|---|
| `TableSnapshot` | `table`, `timestamp` (UTC ISO-8601), `n_rows`, `columns`, `dtypes`, `null_rates` (% of nulls per column, 0 to 100, 4 decimals), `numeric_stats` (`mean` and population `std` of each numeric column) |
| `build_snapshot(table_name, df)` | Computes the snapshot |
| `append_snapshot(snapshot, history_dir)` | Appends one line to `<table>.jsonl` |
| `load_history(table_name, history_dir)` | Reads every line in chronological order; `[]` if there is no file |

**Decisions.**

- **JSON Lines, append-only:** one line per run, easy to read and audit, no database (D7).
- **Population standard deviation (`ddof=0`):** the snapshot describes the whole table, not a sample.
- **Only means and deviations, not the data:** the history stays small (a few KB per table) even after hundreds of runs.

---

## 4.6 `src/quality/dimensions_dama.py`

**What it does.** Measures four DAMA dimensions, as percentages, on staging.

| Function | Parameters | Returns |
|---|---|---|
| `completeness(df)` | DataFrame | `{column: % non-null}`; `None` per column if the table is empty |
| `uniqueness(df, key_columns)` | DataFrame, PK columns | % of rows that do not repeat the key; `None` if no usable PK |
| `validity(df, contract)` | DataFrame, contract | `{column: % valid}`, using `column_rule_violations` |
| `consistency(df, foreign_keys, reference_values)` | DataFrame, catalog FKs, lookup function | `{"col -> table.col": % found}`; `None` if it cannot be evaluated |
| `score_table(df, contract, table_meta, reference_values)` | — | the four dimensions + `summary` with the worst value of each |
| `run()` | — | the full scorecard |

`StagingReferenceValues` is the lookup function used in production. It reads **only the referenced column** from Parquet, keeps the distinct values in a `pd.Index`, and caches them per (table, column).

**Decisions.**

- **Dependency injection in `consistency`:** the function does not know where the parent values come from. In the test they come from a dictionary; in production, from Parquet.
- **Columnar read with a cache:** `orders.order_id` is referenced by three tables and is read only once; `geolocation` (1 million rows, 5 columns) is read on a single column.
- **`pd.Index` for `isin`:** hash lookup, O(1) per element.
- **Uniqueness with the default `duplicated()` (`keep="first"`):** measures how many rows remain if each key is kept once. The validator uses `keep=False`, because there the goal is to list the conflicts.
- **Summary by worst value:** one bad column compromises the table; an average would hide it.
- **Completeness summarized over required columns only:** an optional column has no fill requirement. In the details, optional columns are marked *(optional)* and get no ⚠️.

**Outputs:** `reports/dq_scorecards/dama_scorecard_<timestamp>.json` and `.md`.

---

## 4.7 `src/quality/checks_runner.py`

**What it does.** Runs the pillars on every table, evaluates the SLOs and, for each anomaly, alerts and records an incident.

| Function | Parameters | Returns |
|---|---|---|
| `occurrence_time(finding, snapshot_at, params)` | finding, snapshot timestamp, pillar parameters | when the problem started |
| `handle_anomaly(finding, table_meta, snapshot_at, params, graph, known_incidents, alerts_dir, incidents_dir)` | — | — (alerts and records the incident, unless it already exists) |
| `evaluate_catalog(catalog, slos, history_dir, contracts_dir)` | — | `{table: {pillar: PillarFinding}}`, **without side effects** |
| `run()` | — | `(findings_by_table, slo_results)` |
| `main()` | — | exit code: 1 if some SLO was breached |

**`occurrence_time`**, the basis of a realistic TTD:

- Data pillars (volume, schema, distribution): the problem started when the anomalous snapshot was written, that is, at the ingestion that brought the data.
- Freshness: the problem started when the delay crossed the SLO (last load + `max_delay_hours`).

**`handle_anomaly`**, step by step:

1. If an incident already exists for (table, pillar, snapshot), it does nothing (deduplication, D10).
2. Computes the downstream impacted tables from the lineage graph.
3. Sets severity from the table criticality (`SEVERITY_BY_CRITICALITY`: `high` → `critical`, `medium` → `warning`, `low` → `info`).
4. Sends the alert and records the incident with `occurred_at`, `severity` and `snapshot_at`.

**Why `evaluate_catalog` exists.** Certification and the dashboard need the pillar verdicts, but must not raise alerts or open incidents. This function delivers the verdicts without side effects.

**Output:** `reports/dq_scorecards/pillars_report_<timestamp>.md`, with SLOs at the top and pillars per table.

---

## 4.8 `src/monitoring/anomaly_detection.py`

**What it does.** Answers "is this value anomalous compared with the history?". It knows nothing about tables, SLOs or business.

### Constants

| Constant | Value | Meaning |
|---|---|---|
| `DEFAULT_WINDOW` | 10 | Recent runs used as the baseline |
| `DEFAULT_EWMA_ALPHA` | 0.3 | Weight of the most recent run in the EWMA |
| `DEFAULT_MIN_REL_STD` | 0.01 | Standard deviation floor: 1% of the mean |
| `_MAD_TO_STD` | 1.4826 | Converts the MAD into the equivalent standard deviation of a normal distribution |
| `METHODS` | `zscore`, `moving`, `ewma` | Accepted methods |

### Functions

| Function | Parameters | Returns |
|---|---|---|
| `zscore_check(current_value, historical_values, threshold=3.0)` | current value, history, threshold | `ZScoreResult(is_anomaly, z_score, historical_mean, historical_std)` |
| `moving_check(current_value, historical_values, threshold=3.0, window=10, method="moving", ewma_alpha=0.3, prune_outliers=True, min_rel_std=0.01)` | — | `MovingResult` (the above + `method`, `window_used`, `pruned`) |
| `detect(current_value, historical_values, threshold=3.0, method="zscore", window=10, **kwargs)` | — | single entry point: picks the detector by `method` |
| `_prune_outliers(values, threshold, min_rel_std)` | — | values without outliers |
| `_ewma_stats(values, alpha)` | — | exponentially weighted `(mean, std)` |

### How `moving_check` works

1. Takes the last `window` runs.
2. With fewer than 2 points, returns "not an anomaly" and `z_score = None`.
3. Prunes outliers: computes the median and the MAD (median absolute deviation) and drops points more than `threshold` robust deviations away from the median. It never prunes below 2 remaining points.
4. Computes mean and deviation: simple (`moving`) or exponentially weighted (`ewma`).
5. Applies the floor: `std = max(std, min_rel_std × |mean|)`.
6. `z = (current − mean) / std`; anomaly if `|z| > threshold`.

**Why median and MAD for pruning.** The mean and standard deviation are themselves distorted by the outlier you want to remove. The median and MAD are robust statistics: an extreme point barely moves them.

**Why the 1% floor.** With a constant history (always 99,441 rows), the deviation is zero, and one row less would give an infinite z. With the floor, the minimum deviation is ~994 rows, and only variations above ~3% (3 × 1%) fire. The `volume_drop` scenario (−8%) gave exactly z = −8.00 for this reason.

**The EWMA recurrence** (`_ewma_stats`), applied to each value `v`:

```
diff = v - mean
mean = mean + α·diff
var  = (1 - α)·(var + diff·α·diff)
```

This is the standard incremental form of the exponentially weighted mean and variance.

---

## 4.9 `src/quality/pillars/`

Every pillar returns a `PillarFinding` (`base.py`): `table`, `pillar`, `status` (`ok`, `anomaly` or `insufficient_history`) and `detail` (readable text). `MIN_HISTORY_FOR_ZSCORE = 3` is the minimum number of **previous** runs before a statistical baseline is trusted.

| Pillar | Function | Main parameters | Logic |
|---|---|---|---|
| Freshness | `check_freshness(table_name, history, max_delay_hours, now=None)` | max delay in hours; `now` injectable for tests | Hours since the `timestamp` of the last snapshot. Anomaly if over the limit |
| Volume | `check_volume(table_name, current_n_rows, history, min_expected_volume=None, z_threshold=3.0, method="moving", window=10)` | — | Compares current `n_rows` with `history[:-1]`. With fewer than 3 previous runs, falls back to the contract's `min_expected_volume` |
| Schema | `check_schema(table_name, history)` | — | Added and removed columns and changed dtypes versus the previous run. Needs 2 snapshots |
| Distribution | `check_distribution(table_name, history, z_threshold=3.0, method="moving", window=10)` | — | For each numeric column, detects a shift in the mean; for each column, a shift in the null rate. Lists every anomalous column |
| Lineage | `build_lineage_graph(catalog)`, `downstream_impact(table, graph)`, `upstream_dependencies(table, graph)` | — | Bidirectional graph from the FKs; transitive breadth-first search |

**Decisions.**

- **`history[-1]` is the current run.** The runner runs right after ingestion, and the pillars compare the last snapshot with the previous ones.
- **Injectable `now` in freshness:** without it, the test would depend on the clock and break over time.
- **Lineage with `deque` and a single `_traverse(direction)`:** `popleft()` is O(1) (the original version used `list.pop(0)`, which is O(n)), and one function serves both directions. Edges are recorded without repetition.
- **The default is `method="moving"`:** the tests document cases where the global Z-score fails (contamination, trend).

---

## 4.10 `src/quality/sli_slo.py`

**What it does.** Turns the YAML SLOs into pillar parameters, evaluates the pillars and builds the SLO results.

| Element | Parameters | Returns / content |
|---|---|---|
| `PILLARS` | — | `("freshness", "volume", "schema", "distribution")` |
| `DEFAULT_PILLAR_PARAMS` | — | freshness: `max_delay_hours: 24`; volume and distribution: `z_threshold: 3.0, method: moving, window: 10`; schema: `{}` |
| `SLOResult` | — | `slo_id`, `table`, `pillar`, `description`, `passed`, `finding` |
| `load_slos(path)` | path to `slo.yaml` | list of SLOs; `ValueError` for an unknown pillar |
| `pillar_params(table, pillar, slos)` | — | default parameters overridden by the pair's SLO, if any |
| `evaluate_pillar(table, pillar, history, contract, params)` | — | `PillarFinding`: the single dispatch to the four pillars |
| `evaluate_table(table, history, contract, slos)` | — | `(findings per pillar, parameters per pillar)` |
| `evaluate_slos(slos, findings_by_table)` | — | list of `SLOResult` that **read** already computed findings |
| `evaluate_all(history_by_table, contracts_by_table, slos)` | — | convenience: evaluates only pairs that have an SLO |

**Decisions.**

- **One evaluation per (table, pillar)** (D5): the SLO recomputes nothing; it reads the finding.
- **`pillar_params` copies the defaults** (`dict(...)`), so one SLO does not change the defaults of other tables, and it filters out metadata (`id`, `table`, `pillar`, `description`).
- **Volume without history returns `insufficient_history`**, instead of pretending `n_rows = 0`, which would raise a false alarm against the minimum volume.
- **`passed = status != "anomaly"`:** `insufficient_history` does not fail the SLO (D9).

---

## 4.11 `src/monitoring/alerting.py`

| Element | Detail |
|---|---|
| `Severity` | `"info"`, `"warning"` or `"critical"` |
| `send_alert(message, severity="warning", alerts_dir=None)` | Prints `[ALERT-<SEVERITY>] message`, appends `{timestamp, severity, message}` to `alerts.jsonl` and, if `ALERT_WEBHOOK_URL` is set, posts `{"text": ...}` |
| `_post_webhook(url, text)` | Accepts only `https://`; 3s timeout; catches `(OSError, ValueError)` |

**Decisions:** no emoji in the console (D15); a webhook failure never brings the pipeline down (D17); payload `{"text": ...}`, the format Slack webhooks accept.

---

## 4.12 `src/incidents/incident_log.py`

| Element | Detail |
|---|---|
| `Incident` | `incident_id` (UUID4), `table`, `pillar`, `detail`, `affected_tables`, `occurred_at`, `detected_at`, `ttd_seconds`, `severity`, `snapshot_at` |
| `register_incident(table, pillar, detail, affected_tables, incidents_dir, occurred_at=None, severity="warning", snapshot_at=None)` | Creates the incident and appends it to `incidents.jsonl`; without `occurred_at`, TTD is 0 |
| `load_incidents(incidents_dir)` | List of incidents |
| `find_incident(incidents, table, pillar, snapshot_at)` | Incident already recorded for the triple; `None` if `snapshot_at` is `None` (never dedupes without a snapshot) |
| `Resolution` | `incident_id`, `resolution_summary`, `resolved_at`, `ttr_seconds` |
| `register_resolution(incident_id, resolution_summary, incidents_dir, detected_at=None, resolved_at=None)` | Writes to `resolutions.jsonl`; recovers `detected_at` from the incident; `ValueError` if the incident does not exist **or was already resolved** |
| `open_incidents(incidents, resolutions)` | Incidents without a resolution |
| `compute_ttd_seconds` / `compute_ttr_seconds` | Difference in seconds |

**Decisions.**

- **Resolutions in a separate file, not by editing the incident:** append-only preserves history (when it was detected, when it was resolved) without rewriting lines.
- **Double resolution is rejected:** it would count twice in the mean TTR and could push the resolved percentage above 100%.

---

## 4.13 `src/incidents/rca.py`

| Element | Detail |
|---|---|
| `RCA` | `incident_id`, `whys` (list of answers to the "whys"), `root_cause`, `conducted_by`, `conducted_at` |
| `save_rca(rca, incidents_dir)` | Appends to `rca.jsonl` |
| `load_rca_for_incident(incident_id, incidents_dir)` | The **latest** RCA of the incident (an investigation can be redone) |
| `render_postmortem(incident, rca, timeline, impact, corrective_actions)` | Markdown with summary, timeline, impact, 5 Whys, root cause, corrective actions and metadata |
| `save_postmortem(incident_id, markdown, incidents_dir)` | Writes `postmortems/<incident_id>.md` |

**Decision:** it is blameless by design. The model has no field for "who caused the error", only for who investigated and which corrective actions were taken. The code's structure enforces the culture the book recommends.

---

## 4.14 `src/incidents/cli.py`

**What it does.** It is the human entry point to the incident process. Without it, resolving an incident would require writing Python, and TTR would never be measured.

| Subcommand | Arguments | Effect |
|---|---|---|
| `list` | `--all` (include resolved) | Lists short id, severity, table/pillar, date and detail |
| `resolve <id>` | `--summary` | Records the resolution and shows the TTR |
| `rca <id>` | `--why` (repeatable), `--root-cause`, `--by` | Records the RCA |
| `postmortem <id>` | `--impact`, `--timeline` (repeatable), `--action` (repeatable) | Generates the postmortem; requires an RCA first |

**Decisions:**

- **`find_by_prefix`** accepts any unique prefix of the UUID, like commit hashes in Git. An ambiguous or unknown prefix gives a clear error.
- **`main()` returns 1** on usage errors.

---

## 4.15 `src/transform/build_curated.py`

| Element | Detail |
|---|---|
| `CURATED_SOURCES` | `{"order_items", "orders"}`: tables the layer depends on (used by the orchestrator) |
| `curate_order_items(order_items)` | Pure. Adds `item_total_value = price + freight_value` |
| `curate_orders(orders, order_items_curated)` | Pure. Adds `delivery_delay_days` (`Int64`) and `order_total_value` (sum per order) |
| `run()` | Reads staging, writes `data/curated/*.parquet` and returns the row counts |

**Decisions.**

- **`item_total_value`, not `total_order_value`:** the value belongs to **one item**, and 9,803 orders have more than one item. The previous name led consumers to sum it wrongly: the same semantic trap the catalog documents for `customer_id`.
- **`order_total_value` is null for orders without items:** "no items" (an order canceled before invoicing) is not the same as "zero value".
- **`merge(..., validate="one_to_one")`:** if the per-order aggregation produced duplicates by mistake, pandas raises an exception instead of silently multiplying rows.
- **Positive `delivery_delay_days` = late.** The rounding of `.dt.days` was checked: since the estimated date is always midnight, the result matches the calendar-day difference for all 96,476 deliveries.

---

## 4.16 `src/lineage/field_lineage.py`

| Element | Detail |
|---|---|
| `FieldTransformation` | `frozen`: `source_table`, `source_fields` (tuple), `transformation` (text), `target_table`, `target_field` |
| `FIELD_LINEAGE_REGISTRY` | The three transformations of the curated layer |
| `lineage_for_target_field(table, field)` | Registry entry or `None` |
| `derived_fields(table)` | Fields of a table produced by a transformation |
| `trace_to_source_fields(table, field, _depth=0, _max_depth=10)` | Recursively resolves down to source fields, without duplicates; `RecursionError` if the maximum depth is exceeded (protection against cycles) |
| `describe_lineage(table, field)` | Readable text |

Example, with two levels of recursion:

```python
>>> trace_to_source_fields("orders_curated", "order_total_value")
[('order_items', 'price'), ('order_items', 'freight_value')]
```

**Decision:** `frozen=True` with tuples makes the registry immutable; no code can change it at runtime.

---

## 4.17 `src/certification/certify.py`

| Function | Parameters | Returns |
|---|---|---|
| `certify_table(table_name, table_meta, owner_default, findings, table_open_incidents)` | — | `CertificationResult(table, certified, owner, reasons)` |
| `certify_all(catalog, findings_by_table, unresolved)` | — | Pure: one result per table |
| `evaluate_certification(settings, catalog, findings_by_table=None)` | — | Loads findings (if not given) and open incidents, and certifies |
| `write_status(results, output_path)` | — | Writes the status YAML |
| `run(findings_by_table=None)` | — | Certifies, prints and writes |

**Criteria** (all must hold; each failure becomes a reason in `reasons`):

1. owner defined;
2. history exists;
3. no pillar in `insufficient_history`;
4. no pillar in `anomaly`;
5. no open incident.

**Output:** `reports/certification/certification_status.yaml`.

---

## 4.18 `src/reporting/maturity_dashboard.py`

| Function | Detail |
|---|---|
| `compute_kpis(incidents, resolutions, slo_results, certification_results)` | Pure. Incidents (total, open, distinct resolved, by severity), mean TTD, mean TTR, % of SLOs, % certified |
| `build_dashboard(findings_by_table=None, certification_results=None)` | Reuses what is passed in; computes the rest without side effects |
| `render_dashboard_markdown(dashboard)` | Markdown with KPIs, SLOs and certification |
| `_fmt_duration(seconds)` | s below 1 min, min below 1 h, h above |
| `run(...)` | Writes `reports/maturity_dashboard/dashboard_<timestamp>.md` and prints a summary |

**Decisions.**

- **Resolutions of unknown incidents are ignored**, and resolved incidents are counted by distinct id.
- **The dashboard does not write the certification:** it used to call `run_certification()`, and building one report rewrote another.

---

## 4.19 `orchestration/local_pipeline.py`

Six steps: ingestion → DAMA → pillars → certification → curated layer (gated) → dashboard. Then it prints the summary (blocked tables, breached SLOs, certified tables) and returns the exit code (D16). The findings from step 3 are passed to steps 4 and 6.

---

## 4.20 `scripts/simulate_incident.py`

Each scenario changes **one** raw CSV to exercise a specific mechanism. The first change copies `data/raw/` to `data/raw_backup/`, and `restore` puts the originals back.

| Scenario | Table | Change | Mechanism exercised |
|---|---|---|---|
| `contract_violation` | order_items | 10 prices = −1 | Contract + circuit breaker |
| `volume_drop` | orders | Removes the last 8% of rows | Volume pillar (passes the contract: 91,485 ≥ 90,000) |
| `distribution_shift` | order_items | `freight_value × 3` | Distribution pillar (freight has no maximum in the contract) |
| `null_spike` | products | 30% of categories become null | Null rate (`nullable: true` column) |
| `schema_drift` | order_reviews | New column `review_channel` | The contract only warns; the schema pillar fires |

The real outputs of each scenario are in [6.5](06-running-and-testing.md#65-simulating-incidents).

---

## 4.21 `tests/`

| File | What it guarantees |
|---|---|
| `conftest.py` | Shared sample history (`stable_history`) and a fixed `NOW` |
| `test_type_casting.py` | Conversions; 1.5 as an integer becomes null; the original is not modified |
| `test_contract_validator.py` | Each contract rule fires when it should, and only then |
| `test_contracts.py` | Catalog ↔ contracts consistency (every table has a contract, known types, PK columns declared) |
| `test_dimensions_dama.py` | The four dimensions, the reference cache and the summary |
| `test_anomaly_detection.py` | Z-score, moving window, EWMA, pruning, and the contamination and trend cases |
| `test_pillars.py` | The five pillars |
| `test_slo.py` | Parameter resolution, SLO reading the finding, consistency of `slo.yaml` |
| `test_incidents.py` | TTD, TTR, deduplication, double resolution, open incidents |
| `test_checks_runner.py` | Severity, impact, deduplication and `occurrence_time` |
| `test_curated.py` | Transformations and the field-level lineage guard test |
| `test_certification.py` | The five certification criteria |
| `test_rca.py` | RCA, postmortem and the CLI |
| `test_maturity_dashboard.py` | KPIs and rendering |
| `test_simulate_incident.py` | The simulator scenarios |

Recurring techniques: fixtures (fresh data per test), `tmp_path` (temporary folder deleted afterwards), `monkeypatch` (redirecting the CLI to a temporary folder), `parametrize` (one test with several cases), `pytest.raises` (expected error), and numbers chosen to give exact results (4 rows → each row is worth 25%).
