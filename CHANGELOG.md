# Changelog

## Unreleased

- `--notify-webhook` supports Discord webhooks: the summary is sent as `{"content": ...}` and cut to Discord's 2,000 character limit.
- **Write a proven fix to a Snowflake clone** (#97, first part of safe apply, #96). `dataranger.connectors.snowflake_apply.write_fix_to_clone` clones the patrolled table and replays the fix on the clone as row-level statements, so columns the patrol never read stay as they were. The patrolled table is never written to, and nothing in `patrol` calls this yet: a patrol is still three `SELECT` statements. Fixes that reload rows from the source, and duplicate copies that differ only in a column the patrol did not read, are refused with a clear message.

## 0.7.0 (2026-11-22)

Five fixes built from feedback on the launch post — thank you to everyone who commented, especially Sabarinathan Govindaraj and Lavanya Aalla, whose suggestions became two of them.

- **Flags undocumented tables and columns** (Sabarinathan Govindaraj). `patrol` now also checks how much of the source is actually documented — reading descriptions and owners from dbt's manifest, DuckDB's own comments, or Snowflake's `INFORMATION_SCHEMA`. The CLI summary, `metadata.csv` and the dashboard all show the coverage line; `--skip-metadata-check` turns it off.
- **Catches errors that reconcile at the totals level** (Lavanya Aalla). A row count and amount matching the source doesn't prove each row is right — two rows can swap values and the totals stay exactly the same. `residual()` now also checks optional extra control measures (`hash_total`, `distinct_<column>`, `min_amount`/`max_amount`) whenever a manifest publishes them; a manifest without them behaves exactly as before.
- **A lateness window for late-arriving records.** `patrol --lateness-days N` gives a day N days of grace before a shortfall against its control totals is reported as broken, since it may just be waiting on records that haven't landed yet. A shortfall inside the window is "still open" instead of an incident, and gets re-checked on the next patrol. Wired into the CLI, the dbt `meta.dataranger` block, and all four MCP server tools.
- **Control totals straight from a flat file's own trailer record.** `dataranger totals --from-trailer` reads one control total from a delivery file's own trailer line or a sidecar control file — delimited or fixed-width — which is stronger evidence than a totals table computed downstream, since the sender wrote it. `--compare-to` optionally checks it against computed totals.
- **Escalates an ambiguous tie instead of guessing.** When two candidate fixes both reconcile against the source equally well but leave the table in different states, the gate now escalates rather than silently picking one (`--allow-ambiguous-autofix` restores the old behavior). The dashboard shows the competing fixes side by side.
- `dataranger totals` builds the control totals `patrol` needs from the raw or landing table the facts are loaded from, for teams that don't keep such a table: `--db` with `--table`, or `--facts-file`, written with `--out` (CSV, TSV or Parquet) or `--out-table` (DuckDB), using the same `--date`, `--segment` and `--amount` flags.

## 0.6.0 (2026-11-18)

More warehouses for dbt projects, alerts where your team already works, and less to configure. All five changes below were contributed by @sarankumar1325.

- **dbt on Postgres.** `--dbt-model` now patrols models on a Postgres target. The session is opened read-only and named `dataranger`, the profile's `role` is applied, and psycopg 3 or psycopg2 both work. Install with `pip install "dataranger[dbt,postgres]"`.
- **dbt on BigQuery (experimental).** `--dbt-model` also patrols models on a BigQuery target, with `oauth`, `service-account` and `service-account-json` profiles. If the profile sets `maximum_bytes_billed`, every query carries that cap. BigQuery bills by bytes scanned, so use `--days` to keep the scan small. It has so far only run against a stand-in client, so please report what you see on a real project. Install with `pip install "dataranger[dbt,bigquery]"`.
- `patrol --threshold` must be from 0 to 0.5 (the calibration grid's range), and `calibrate --alpha` and `--delta` must be strictly between 0 and 1. Other values now stop with a short usage error (exit 2) instead of being accepted. An empty or header-only `--labels` file also gets a one-line error instead of a pandas traceback.
- `--dbt-model`: when neither the command line nor `meta.dataranger` sets the key, a `unique` test on one column of the model gives it. Likewise, a `relationships` test gives `reference`, `ref_key` and `ref_model`. Two tests of a kind on one model mean no guess, and a line on stderr says which setting to add. The order of preference is command line, `meta`, tests, defaults.
- `patrol --notify-webhook URL` (or `DATARANGER_WEBHOOK`) posts the patrol summary to a Slack or Teams incoming webhook (an Adaptive Card for Teams Workflows URLs) when an incident needs a person; `--notify-always` posts after every patrol. A failed post is a note on stderr and doesn't change the exit code, and the URL is never printed.

## 0.5.4 (2026-11-15)

- `python -m dataranger` now works, for when pip installs the `dataranger` command into a folder that is not on PATH (common on Windows).
- README: how DataRanger compares with tests, observability platforms and AI agents, and what data leaves your machine (none, in normal use).
- OpenSSF Best Practices passing badge. Added a security policy with private reporting and a 7-day first response, a code of conduct, and issue and pull request templates.
- `ruff` runs in CI on every change, and the CI and release workflows run with a read-only token by default.

## 0.5.3 (2026-11-15)

- Version number brought in line with the release tag. The `v0.5.2` tag (Zenodo 10.5281/zenodo.24240657) holds the same code but still reported 0.5.1, so its PyPI publish was refused; the release workflow now stops at once if the tag and the package version differ.

- `dataranger.repair.cells.certify_remainder` and `remainder_cutoff`: an exact certificate for cell repair on a finite table. Label a random sample of rows; with probability at least 1 - delta, at most a share alpha of the rows nobody labeled end with a wrong change. Checked by simulation in `tests/test_finite_population.py`.
- Real revisions of a public feed (`benchmarks/real_feed/`, `results/real_feed/`): the gate on three years of the Johns Hopkins COVID-19 US state reports, with 150 state-days adjudicated by hand.
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
