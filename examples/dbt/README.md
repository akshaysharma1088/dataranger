# Patrol a dbt model

A small dbt project that runs on a local DuckDB file, so you can try DataRanger's dbt source without a warehouse or credentials.

```bash
pip install "dataranger[dbt]" dbt-duckdb
cd examples/dbt
python make_seeds.py                 # writes demo data with planted problems as dbt seeds
dbt build --profiles-dir .
dataranger patrol --dbt-model fct_payments
```

Open `dataranger-report/dashboard.html` for the report.

## How it is wired

- `models/schema.yml` holds the column mapping in the fact model's `meta: dataranger:` block, next to the model it describes. It also names the reference model (`dim_customers`) and the control totals (`control_totals`).
- DataRanger reads `target/manifest.json` to find the table each model was built as, and `profiles.yml` to connect. It runs three `SELECT` statements and never runs dbt or changes a table.
- `models/control_totals.sql` is the piece most projects have to add: what the source system says it sent, one row per day and segment plus a `*` row for the whole day, with `n` (row count) and `amount` (sum). Build it from the source or landing data, not from the fact model you are checking.

## On your own project

1. Add a `meta: dataranger:` block to your fact model, as in `models/schema.yml`.
2. Run `dbt build`, then `dataranger patrol --dbt-model your_model --days 45`.
3. For a Snowflake target, install `dataranger[dbt,snowflake]`. The connection comes from your dbt profile.

DuckDB and Snowflake targets are supported. For other warehouses, export the three tables to Parquet and use `--facts-file`, `--ref-file` and `--manifest-file`.

## In CI

```yaml
- run: dbt build
- uses: akshaysharma1088/dataranger@v0.5.0
  with:
    dbt-model: fct_payments
```
