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

## License

MIT
