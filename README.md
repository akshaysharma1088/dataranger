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

- Synthetic, 5 seeds per size: every injected error found except silent drops in the small size (3 of 10). Every decoy is flagged too, which is the point: detection cannot tell a real spike from an error, so the later stages must.
- Hospital: F1 0.83. Beers: F1 0.98. Flights: F1 0.10 with no configuration, 0.50 when told the entity key (`flight`).

```bash
pip install -e .
dataranger bench --size small --seed 1        # one synthetic case
python benchmarks/run_detection.py            # all sizes and the real datasets
```

## License

MIT
