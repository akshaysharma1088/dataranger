# Daily patrol from an Airflow DAG

[dataranger_patrol.py](dataranger_patrol.py) runs a DataRanger patrol once a day. It uses two `BashOperator` tasks, no custom operator and no extra dependency.

- `make_demo_db` builds the demo database from [`examples/scheduled/make_demo_db.py`](../scheduled/make_demo_db.py). Remove it when you patrol your own tables.
- `patrol` runs `dataranger patrol ... --json --fail-on-escalate`. Exit code 2 means an incident needs a person, and Airflow fails the task on it. Any other run succeeds. The JSON summary is in the task log.

Each run keeps its reports in its own folder, `<report dir>/<run date>/`, with `dashboard.html`, `audit.jsonl`, `incidents.csv` and the rest. To see a day's patrol, open `dashboard.html` in that folder.

## Run it on the demo data

No credentials are needed. Tested with Airflow 3.3.2 on Python 3.10.

```bash
python -m venv .venv && source .venv/bin/activate
pip install apache-airflow dataranger
export AIRFLOW_HOME="$PWD/airflow-home"
export AIRFLOW__CORE__LOAD_EXAMPLES=False
export AIRFLOW__CORE__DAGS_FOLDER="$PWD/examples/airflow"   # run from the DataRanger repository root
export DATARANGER_REPORT_DIR="$PWD/patrol-reports"
airflow db migrate
airflow dags test dataranger_patrol 2026-11-24
ls patrol-reports/2026-11-24
```

The demo data has planted problems, so the `patrol` task ends in `failed` with "The command returned a non-zero exit code 2", and the report folder is still written. That failure is the signal working. On clean data the task succeeds.

To run it on a schedule, start Airflow with `airflow standalone` instead of `dags test`. The DAG runs every day at 06:17 UTC, and `catchup=False` stops it from back-filling old days.

## Point it at your own tables

1. Delete the `make_demo_db` task and the `make_demo_db >> patrol` line.
2. In the `patrol` command, replace `--db`, `--table`, `--ref-table`, `--manifest-table` and the column flags with your own, as in the [scheduled example](../scheduled/README.md#point-it-at-real-tables).
3. Install `dataranger` in the environment that runs your Airflow workers, and move `schedule` to a time after your nightly load.
4. Set `DATARANGER_REPORT_DIR` to a folder the workers can write to and you can open, or change the `--out` path.

Anything that has to happen before a patrol, such as a dbt run, belongs in an upstream task: put it before `patrol` in the dependency line.
