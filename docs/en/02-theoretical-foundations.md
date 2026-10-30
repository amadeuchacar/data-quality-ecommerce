# 2. Theoretical foundations

> [Index](README.md) · Previous: [1. Overview](01-overview.md) · Next: [3. Architecture](03-architecture.md)

This chapter presents the concepts from **Data Quality Fundamentals** used in the project and shows, for each one, where it lives in the code.

The DAMA dimensions (section 2.4) do not come from the book: they are a complement from DAMA UK, included because they provide a gradual measure that the book does not formalize.

## 2.1 Data downtime

**Concept.** Data downtime is the period when data is partial, erroneous, missing or otherwise inaccurate. The book proposes measuring it as:

```
data downtime = number of incidents × (TTD + TTR)
```

- **TTD (time to detection):** how long the problem existed until someone (or something) noticed.
- **TTR (time to resolution):** how long it took from detection to fix.

The formula shows the three levers to reduce downtime: fewer incidents (prevention), faster detection (observability) and faster resolution (incident process).

**In the project.** Each stage works on one lever:

| Lever | Mechanism | Where |
|---|---|---|
| Fewer incidents | The contract blocks bad data before staging | `src/ingestion/` |
| Lower TTD | Pillars compare every run with the history | `src/quality/pillars/`, `src/quality/checks_runner.py` |
| Lower TTR | Incidents are born with impact, severity and context; the CLI guides resolution | `src/incidents/` |
| Measure all three | The dashboard shows incident count, mean TTD and mean TTR | `src/reporting/maturity_dashboard.py` |

TTD is computed realistically: the moment the problem "started" is the timestamp of the snapshot that brought the anomalous data, not the moment of detection (see [4.7](04-code-reference.md#47-srcqualitychecks_runnerpy)).

## 2.2 Declarative tests and data contracts

**Concept.** The first defense is to declare, *before* looking at the data, what it must satisfy: schema, nullability, uniqueness, accepted values, business ranges, minimum volume. It is the data equivalent of unit tests in software. The industry term for this agreement between producer and consumer is **data contract**.

The limit of this approach, which the book stresses, is that tests only catch what someone thought of writing. That is why they are necessary but not sufficient.

**In the project.** One YAML per table in `contracts/`, validated by `validate_dataframe()` (see [4.3](04-code-reference.md#43-srcingestioncontract_validatorpy)). Nine rules, eight of them blocking:

| Rule | Real example it catches |
|---|---|
| Missing column | The contract declared `product_lenght_cm`, but the CSV has `product_length_cm` |
| Extra column (*warning*) | A new column appears in the CSV (schema drift) |
| Non-castable type | Text in `price` |
| Forbidden null | Empty `order_id` |
| Duplicate | Repeated `order_id` |
| Null or duplicated PK (single or composite) | The same `(order_id, order_item_id)` twice |
| Value outside the list | `review_score = 6` |
| Out of range | Negative `price` |
| Volume below the SLA | `orders` with 5 thousand rows instead of 99 thousand |

## 2.3 Circuit breaker

**Concept.** A circuit breaker stops bad data from flowing before it propagates to consumers. The idea comes from electrical engineering and distributed systems: it is better to switch off one part than to let the failure spread.

**In the project.** In `ingest_table()`, if the contract has any *blocking* violation, the function returns before writing the Parquet file. Three decisions matter here:

1. **The breaker is per table, not per pipeline.** A failing `products` does not stop `sellers` from going through.
2. **The blocked table does not write a snapshot.** It keeps "aging" until the freshness SLO fires. This makes the block visible to every consumer without any extra mechanism.
3. **The process exits with code 1**, so an orchestrator (cron, CI, Airflow) can react too.

## 2.4 Quality dimensions (DAMA UK)

**Concept.** DAMA UK defines six quality dimensions: completeness, uniqueness, validity, consistency, timeliness and accuracy. They express quality as a **percentage** instead of pass/fail.

**In the project.** Four dimensions are measured on staging (`src/quality/dimensions_dama.py`). Timeliness belongs to the freshness pillar. Accuracy would require an external source of truth the dataset does not provide.

An important consequence, discussed in [3.3](03-architecture.md#33-architecture-decisions): since only approved data reaches staging, validity and uniqueness are 100% **by construction**. On staging they work as a certification of the gate. The new information comes from two dimensions:

- **Consistency:** it crosses tables, something the contract (which looks at one table at a time) cannot do. It found 278 customers and 7 sellers whose ZIP code prefix has no coordinates in `geolocation`, and product categories without a translation.
- **Completeness of optional columns:** this is profiling, not rejection.

## 2.5 The five pillars of data observability

**Concept.** Observability is the ability to understand the health of the data from the signals it emits. The book organizes these signals into five pillars:

| Pillar | Question | Example failure |
|---|---|---|
| **Freshness** | Is the data up to date? | Yesterday's job did not run |
| **Volume** | Is the amount as expected? | Half of the rows arrived |
| **Schema** | Did the structure change? | Column removed, type changed |
| **Distribution** | Are the values within the usual pattern? | Mean price doubled, nulls spiked |
| **Lineage** | Where does it come from and where does it go? | Who is affected if this table breaks |

The difference from the tests in section 2.2: pillars do not need anyone to foresee the failure. They compare the present with the past.

**In the project.** One module per pillar in `src/quality/pillars/`, all returning the same type (`PillarFinding`). The history comes from `reports/quality_history/<table>.jsonl`, one line per approved ingestion.

| Pillar | How it is measured |
|---|---|
| Freshness | Hours since the last snapshot vs `max_delay_hours` |
| Volume | Z-score of `n_rows` against recent runs |
| Schema | Columns and dtypes vs the previous run |
| Distribution | Z-score of the mean of each numeric column and of the null rate of each column |
| Lineage | Dependency graph built from the catalog `foreign_keys` |

## 2.6 Anomaly detection

**Concept.** Fixed thresholds ("alert if fewer than 90 thousand rows arrive") are fragile: either they are loose and catch nothing, or tight and raise false alarms. The book advocates thresholds **learned from history**. The simplest is the Z-score:

```
z = (current_value - historical_mean) / historical_std
```

A |z| > 3 means the value is more than 3 standard deviations away from the mean, which is unlikely if nothing changed.

**Problems of the global Z-score, and how the project solves them:**

| Problem | Example | Solution in the project |
|---|---|---|
| **Contamination:** an old incident in the history inflates the standard deviation and hides new problems | One day with 7.5×10¹⁰ rows makes a drop from 100 to 500 look normal | Outlier pruning with median/MAD before computing the baseline |
| **Trend:** in a growing series, an old value looks normal | Series from 100 to 390; the value 150 is within the global mean, but it is a huge drop from ~390 | Moving window with the last 10 runs (`method: moving`) |
| **Zero variance:** with a constant history, any difference raises an alarm | Always 99,441 rows; 99,440 fires | Standard deviation floor of 1% of the mean (`min_rel_std`), tolerating variations up to ~3% |
| **Little history** | With 1 or 2 points there is no reliable standard deviation | Below 3 previous runs the result is `insufficient_history` |

EWMA (exponentially weighted moving average), which gives more weight to recent runs, is also available. All of this lives in `src/monitoring/anomaly_detection.py` (see [4.8](04-code-reference.md#48-srcmonitoringanomaly_detectionpy)).

## 2.7 SLA, SLI and SLO

**Concept.** The triad comes from site reliability engineering (SRE), and the book adapts it to data:

- **SLA (agreement):** the commitment to consumers. Example: "`orders` never goes more than 24h without a load."
- **SLI (indicator):** what is measured. Example: hours since the last successful load.
- **SLO (objective):** the numeric target the SLI must meet. Example: `max_delay_hours: 24`.

**In the project.** In `config/slo.yaml`, each SLO names a table, a pillar, a description (the SLA) and the parameters (the SLO). The pillar is the SLI. A central decision (see [3.3](03-architecture.md#33-architecture-decisions)) is that each (table, pillar) pair is evaluated **only once**, with the SLO parameters; the alert, the incident, the SLO and the certification all read the same verdict.

## 2.8 Incident management

**Concept.** Detecting is half the job. The book describes a response process with these elements:

- **Triage with context:** severity, impact and who was affected.
- **Communication:** alerts to the right people.
- **Root cause analysis (RCA):** why the problem was possible.
- **Blameless postmortem:** record the learning without looking for culprits, so people report problems instead of hiding them.

**In the project:**

| Element | Implementation |
|---|---|
| Severity | Table criticality in the catalog: `high` → critical, `medium` → warning, `low` → info |
| Impact | Downstream tables from the lineage graph, stored in the incident |
| Communication | Console + `reports/alerts/alerts.jsonl` + optional webhook (Slack) |
| Record | `reports/incidents/incidents.jsonl`, with TTD |
| No duplicates | One incident per (table, pillar, snapshot) |
| RCA | 5 Whys technique, recorded through the CLI |
| Postmortem | Markdown that is blameless by design: it has a field for "who investigated", never for "who caused it" |
| Resolution | The CLI records the resolution and computes TTR |

## 2.9 Lineage

**Concept.** Lineage answers two incident questions:

- **"Who is affected?"** (downstream impact)
- **"Where did the problem come from?"** (upstream cause)

The book distinguishes **table-level** from **field-level** lineage and argues for reconstructing the whole dependency chain, not only the direct relationship.

**In the project:**

- **Table-level lineage** (`src/quality/pillars/lineage.py`): derived from the catalog foreign keys. If `order_items` references `orders`, a problem in `orders` reaches whoever joins the two tables. This is an approximation, discussed in [7.3](07-study-guide.md#73-known-limitations).
- **Field-level lineage** (`src/lineage/field_lineage.py`): with no SQL to parse (the project uses pandas), each transformation is **declared** in a registry. The recursion rebuilds the full chain: `orders_curated.order_total_value` → `order_items_curated.item_total_value` → `order_items.price` + `order_items.freight_value`. A test fails if the code creates a column without a registry entry.

## 2.10 Data certification

**Concept.** Certification is an explicit "safe to use" seal, granted only when the data meets objective criteria. The book describes steps to get there: observability, defined owners, a definition of "good data", SLAs/SLOs, an incident process, the certification mechanism itself and training people.

**In the project** (`src/certification/certify.py`), a table is certified when it:

1. has an owner (specific, or the catalog `owner_default`);
2. has enough history for **every** pillar to give a verdict;
3. has no pillar in anomaly;
4. has no open incident.

Criterion 2 applies the principle "absence of evidence is not evidence". Criterion 4 connects the incident process to certification: the table is certified again only after someone records the resolution.

## 2.11 Maturity

**Concept.** Data reliability is a practice that matures over time: from reactive (finding problems through consumers) to proactive and automated. To know whether the practice is improving, you have to measure it.

**In the project.** The dashboard (`src/reporting/maturity_dashboard.py`) consolidates on every run:

- incidents (total, open, resolved, by severity);
- mean TTD and mean TTR;
- percentage of SLOs met;
- percentage of certified tables.

With these numbers, the data downtime formula from section 2.1 can be estimated directly.

## 2.12 Summary: from book to code

| Concept | Main file |
|---|---|
| Data downtime (count × (TTD + TTR)) | `src/reporting/maturity_dashboard.py` |
| Declarative tests / contract | `contracts/*.yaml`, `src/ingestion/contract_validator.py` |
| Circuit breaker | `src/ingestion/load_raw.py` (`ingest_table`) |
| DAMA dimensions (complement) | `src/quality/dimensions_dama.py` |
| Freshness, volume, schema, distribution | `src/quality/pillars/*.py` |
| Anomaly detection | `src/monitoring/anomaly_detection.py` |
| SLA / SLI / SLO | `config/slo.yaml`, `src/quality/sli_slo.py` |
| Alerts | `src/monitoring/alerting.py` |
| Incidents, TTD, TTR | `src/incidents/incident_log.py` |
| RCA and blameless postmortem | `src/incidents/rca.py`, `src/incidents/cli.py` |
| Table-level lineage | `src/quality/pillars/lineage.py` |
| Field-level lineage | `src/lineage/field_lineage.py` |
| Certification | `src/certification/certify.py` |
| Maturity | `src/reporting/maturity_dashboard.py` |
