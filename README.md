# DataRanger

**DataRanger patrols your data, fixes what it can prove, and calls you for the rest.**

A park ranger patrols the trails, fixes what is safe to fix on the spot, calls in anything dangerous, and files a report. DataRanger does the same for your tables.

Most data-quality tools stop at an alert. Letting an AI agent fix data is the obvious next step, and a risky one: a wrong fix can pass every monitor you have. DataRanger only applies a fix after it has been **proven against source data**, and it hands everything else to a person with the evidence attached.

## The patrol loop

1. **Detect.** Rules and statistics find nulls, duplicates, schema drift, late partitions, broken references, unit errors and silent shifts. No LLM.
2. **Diagnose.** A language model answers one narrow question: what is the likely cause?
3. **Propose fix.** A templated fix (dedupe, backfill, quarantine, patch). The model fills in parameters only.
4. **Verify.** [pipeverify](https://github.com/akshaysharma1088/pipeverify) checks the fix against source data, on a copy.
5. **Gate.** Proven and low risk: applied as a new version, never an overwrite. Anything else: escalated.
6. **Report.** Every detection, fix and decision lands on a live dashboard and an audit log.

## Try it

```bash
pip install dataranger
dataranger demo                 # patrol a generated table with planted problems; writes dataranger-demo/dashboard.html
```

On your own DuckDB tables (a fact table partitioned by date, a reference table, and the source's control totals):

```bash
dataranger patrol --db warehouse.duckdb --table payments --ref-table customers \
    --manifest-table control_totals --key payment_id --reference customer_id \
    --date booking_date --segment channel --amount amount
```

The control-totals table has one row per date and segment, plus a row with segment `*` for the whole day, with columns `n` (rows) and `amount` (sum). DataRanger never modifies your tables: fixed partitions go to `fixed_version.csv`, every decision and its evidence to `audit.jsonl`, and the morning report to `dashboard.html`.

The default threshold was calibrated on the DQ-Repair benchmark for a 1% target. Once people have reviewed some of DataRanger's decisions, calibrate on your own data:

```bash
dataranger calibrate --audit report/audit.jsonl --labels reviewed.csv --alpha 0.01
```

`reviewed.csv` has columns `partition` and `verdict` (right or wrong). About 300 reviewed incidents certify a 1% target and about 60 certify 5%.

## DQ-Repair benchmark (in progress)

DQ-Repair measures how many data-quality issues an agent can fix safely. This branch adds the benchmark data and the detection stage.

**Synthetic cases.** A transactions table and a customer dimension, generated per seed in three sizes (about 24k, 120k and 480k rows over 60 days). Each case keeps the source of record and a dirty warehouse copy with known issues and the correct fix for each:

| Type | What happens | Correct response |
|---|---|---|
| Duplicates | A retried load appends 1 to 8% of a partition again | Dedupe on `txn_id` |
| Late partition | A day's file arrives after the job ran | Reload the partition |
| Schema drift | Upstream renames `channel` to `payment_channel` | Remap, after review |
| Broken references | A bad key mapping points rows at missing customers | Quarantine, escalate |
| Unit or scale | One channel starts sending cents | Rescale by 0.01 |
| Silent drop | A join change quietly drops one slice of a day | Reload the partition |
| Decoys | A promotion day or a big-ticket travel day, real in the source | Do nothing |

**Real datasets.** Hospital, Flights and Beers, the standard cell-level cleaning datasets with known clean versions, downloaded on first use from the [Raha repository](https://github.com/BigDaMa/raha) at a pinned commit.

**Detection so far** (deterministic, no LLM, `results/detection.json`):

- Synthetic, 5 seeds per size: the statistical monitors find every structural error, 60 to 100% of silent drops, and none of the partial-late partitions. Every decoy is flagged too, which is the point: detection cannot tell a real spike from an error, so the later stages must.
- Hospital: F1 0.83. Beers: F1 0.98. Flights: F1 0.10 with no configuration, 0.50 when told the entity key (`flight`).

```bash
pip install -e .
dataranger bench --size small --seed 1        # one synthetic case
python benchmarks/run_detection.py            # all sizes and the real datasets
```

## Repair with a calibrated gate (first results)

The loop proposes template fixes for each incident, checks every candidate against the source's control totals and the declared invariants (unique keys, valid references), and applies the best-supported fix only if its residual clears a threshold chosen by Learn-then-Test. Fixes that delete data or change meaning always escalate. The source publishes control totals per partition and channel; 15% of them are stale by up to 1%, so even a correct fix can leave a small residual.

250 instances (150 small, 100 medium), 6,150 incidents, 100 random calibration and test splits by instance, delta = 0.05. Silent harm is the share of incidents that end with a wrong fix applied. All numbers are in `results/repair_summary.json`.

| Policy | Auto-applied | Silent harm | Real errors fixed |
|---|---|---|---|
| Rules: default fix for every alert | 64.6% | 25.7% | 56.0% |
| Agent checked by the monitors | 55.1% | 17.3% | 56.8% |
| Verified, calibrated for alpha = 1% | 59.9% | 0.57% | 52.5% |
| Verified, calibrated for alpha = 2% | 69.8% | 1.36% | 55.2% |
| Verified, calibrated for alpha = 5% | 77.8% | 3.92% | 56.7% |

- **The bound holds.** Test harm exceeded alpha in 0 to 3% of splits, within delta = 5%, for both evidence granularities.
- **Hand-picked thresholds do not travel.** A threshold of 0.01 is safe with per-channel totals but breaks a 1% target in every split when only partition totals exist; calibration moves the threshold to 0.004 on its own.
- **Exchangeability matters.** Calibrated without any partial-late incidents, the gate then wrongly applies "no action" to 68% of them. New incident types need recalibration, which is what epoch recalibration with repair memory is for.
- **Source-aware detection.** The statistical monitors miss every partial-late partition; a reconciliation check against the control totals catches all of them.

```bash
python benchmarks/run_repair.py 150 100       # about 6 minutes on two cores
python benchmarks/analyze_repair.py
```

### Language models as proposers

Qwen2.5 7B and 14B, served by Ollama on free GitHub Actions runners (`.github/workflows/llm.yml`, triggered by pushing to the `llm-run` branch; responses are committed to `llm-results`), proposed one fix per incident. At a 5% target:

| Model | Policy | Automated | Harm | Real errors fixed |
|---|---|---|---|---|
| 7B | Apply the model's fix | 91.4% | 37.3% | 27.9% |
| 7B | Apply it if the monitors go quiet | 62.2% | 22.6% | 27.3% |
| 7B | Through the gate | 50.0% | 3.05% | 27.0% |
| 7B | Gate over model + templates | 76.5% | 3.04% | 58.2% |
| 14B | Apply the model's fix | 91.3% | 30.4% | 43.2% |
| 14B | Through the gate | 53.7% | 2.27% | 40.5% |

The models' fixes were right for 54% (7B) and 61% (14B) of incidents. The gate kept harm under target for both; it cannot make a weak proposer strong, but it makes any proposer safe. `python benchmarks/eval_llm.py <runs/<id>/llm>` reproduces the table.

### Larger tables

On 40 large instances (about 480k rows each, 981 incidents), a threshold calibrated on small and medium tables gave 1.02%, 2.04% and 5.71% harm at targets of 1, 2 and 5%: at or just above target, since relative gaps shrink as partitions grow. Calibrating separately for "no action" decisions and for repairs (`calibrate_groups`) transferred with margin: 0.92%, 1.33% and 3.87%.

### Repair memory under drift

`benchmarks/run_stream.py` simulates a year: five data estates of ten tables each, a new incident type (partial-late partitions) from month 5, and staler control totals from month 9. At a 2% target:

| Policy | Silent harm | Automated | Months over target |
|---|---|---|---|
| Calibrated once | 4.63% | 83.4% | 89% |
| Recalibrated monthly | 1.46% | 70.9% | 27% |
| Monthly + repair memory | 0.51% | 55.3% | 11% (all in month 5) |

Memory (`dataranger.repair.memory`) escalates unfamiliar incident signatures, blocks signatures whose fixes went wrong, and recalibrates on what it would have done. It recovered from the new incident type within a month (harm on partial-late incidents 38% in month 5, 3% in month 6). The price is automation: when the world changes, staying inside the promise means asking people more often.

### Real datasets: Hospital, Flights, Beers

No source of record here, so the evidence comes from the table itself (agreeing dependencies, normalization rules). Half the rows are labels: a quarter fits an evidence score and a quarter certifies the threshold. Compared on held-out rows with Baran (20 labeled tuples, Raha detection) and conformal data cleaning (reimplemented, trained on clean rows):

| Data | Method | Errors fixed | Precision | Wrong per 1k cells |
|---|---|---|---|---|
| Hospital | Baran | 55.0% | 93.5% | 0.96 |
| Hospital | Conformal cleaning | 85.0% | 64.7% | 11.80 |
| Hospital | DataRanger, alpha 5% | 62.8% | 98.2% | 0.37 |
| Flights | Baran | 51.2% | 81.7% | 34.37 |
| Flights | Conformal cleaning | 51.6% | 94.6% | 8.83 |
| Flights | DataRanger, alpha 5% | 25.0% | 95.3% | 3.67 |
| Beers | Baran | 89.2% | 94.0% | 9.46 |
| Beers | Conformal cleaning | 64.9% | 60.2% | 70.40 |
| Beers | DataRanger, alpha 1% | 85.0% | 100.0% | 0.02 |

The target held in every split. Baran's corrections are in `results/baran/`; `benchmarks/run_baran.py` reproduces them (it needs `pip install raha`).

## License

MIT
