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

## Status

Early. Version 0.0.1 reserves the name and ships the command-line skeleton:

```bash
pip install dataranger
dataranger plan
```

The first working patrol loop (DuckDB, open-weights models through Ollama, static dashboard) is planned for 0.1.0, alongside the DQ-Repair benchmark.

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

The proposer here enumerates fix templates without a language model. The LLM proposer (Qwen2.5 through Ollama) is next.

## License

MIT
