# Changelog

## Unreleased

- `dataranger.repair.cells.certify_remainder` and `remainder_cutoff`: an exact certificate for cell repair on a finite table. Label a random sample of rows; with probability at least 1 - delta, at most a share alpha of the rows nobody labeled end with a wrong change. Checked by simulation in `tests/test_finite_population.py`.
- Benchmarks for the paper revision: certification per table on 1,000 held-out tables (`benchmarks/analyze_cluster.py`), escalation reasons and the harder mix certified per table (`benchmarks/analyze_cluster_extra.py`, `benchmarks/run_hard_cohort.py`), cell repair with the exact certificate (`benchmarks/run_cells2.py`), and the paper's table rows generated from the result files (`benchmarks/paper_tables.py`).

## 0.5.1 (2026-11-13)

- First PyPI release of the 0.5 line. The `v0.5.0` tag works for the GitHub Action, but its PyPI publish stopped at the release tests, which ran without the test dependencies. The release workflow now installs them the same way CI does.
- Snowflake: `SNOWFLAKE_PASSCODE` for multi-factor logins, a demo you can run on a trial account in `examples/snowflake`, and the connector's own `FutureWarning` no longer prints.

## 0.5.0 (2026-11-13)

DataRanger now reads from where the data lives and runs in CI.

- **Snowflake source.** `dataranger patrol --snowflake` reads a fact table, its reference table and control totals with three read-only queries. Credentials come from `SNOWFLAKE_*` environment variables or a named connection. Install with `pip install "dataranger[snowflake]"`.
- **dbt source.** `dataranger patrol --dbt-model fct_payments` patrols the table behind a dbt model, on DuckDB or Snowflake targets. The column mapping can live in the model's `meta: dataranger:` block. Install with `pip install "dataranger[dbt]"`. A runnable project is in `examples/dbt/`.
- **GitHub Action.** `uses: akshaysharma1088/dataranger@v0.5.0` runs a patrol in CI, writes a job summary, uploads the report and fails the job when an incident needs a person.
- `--days N` patrols only the most recent days, for every source.
- `--summary-md PATH` appends a Markdown summary of the patrol to a file.
- Two new MCP tools, `patrol_snowflake` and `patrol_dbt`.

## 0.4.2 (2026-11-12)

Clearer MCP tool definitions, so agents pick the right tool and use it correctly.

- Every tool now says what a patrol checks, when to use it instead of its siblings, what it reads and writes, and what it returns.
- Every parameter has its own description in the tool schema, and the patrol tools declare an output schema.
- Tools carry MCP hints: `get_incident` is marked read-only, and none are destructive or reach the network.
- Requires `mcp>=1.23`.

## 0.4.1 (2026-11-12)

- Listed in the official [MCP Registry](https://registry.modelcontextprotocol.io) as `io.github.akshaysharma1088/dataranger`. Each release now publishes there automatically.
- `mcp` is now a core dependency, so `uvx dataranger mcp` runs the server with nothing to install first. `pip install "dataranger[mcp]"` still works.
- New Colab demo notebook with an "Open in Colab" badge in the README. (#44, thanks @KingEmma7)

## 0.4.0 (2026-11-12)

DataRanger now works as a guardrail for AI agents.

- **MCP server.** `dataranger mcp` lets Claude, Cursor, VS Code and other MCP clients patrol data before they trust it. Tools: `patrol_duckdb`, `patrol_files`, `get_incident` and `run_demo`. Inputs are opened read-only and nothing is written back. Install with `pip install "dataranger[mcp]"`.
- A wrong column mapping (for example `--key wrong_id`) now stops with a clear error that lists the table's columns, instead of being silently ignored. (#42, thanks @ompatil1906)
- New example: run a nightly patrol with GitHub Actions, in `examples/scheduled/`. (#38, thanks @KingEmma7)

## 0.3.0 (2026-11-11)

DataRanger's first community release: every change below came from outside contributors.

- `patrol` reads CSV, TSV and Parquet files, not only DuckDB tables. (#24, thanks @Hayabusa-YL)
- Schema-qualified table names such as `gold.payments` work with `patrol --db`. (#23, thanks @bfs2021)
- `patrol` prints the fixed / left alone / escalated breakdown, like `demo`. (#25, thanks @kunal-dangi)
- `patrol --json` prints a machine-readable summary, and `--fail-on-escalate` exits with code 2 when a person needs to look. (#27, thanks @Sarbajit-Sengupta)
- `calibrate` validates the labels file and reports bad verdicts with their spreadsheet row numbers. (#34, thanks @sarankumar1325)
- Reports and audit files are written as UTF-8 on every platform. (#33, thanks @jwalith4418-Mj)
- Each patrol also writes `incidents.csv`, one row per incident, for spreadsheet triage. (#36, thanks @Mnvv08)
- Tests for `calibrate` (#26, thanks @Sarbajit-Sengupta), and CI on Python 3.10 to 3.13 (#35, thanks @Mnvv08).

## 0.2.1 (2026-10-30)

- First public release on PyPI.
