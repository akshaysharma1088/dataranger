"""A daily DataRanger patrol as an Airflow DAG.

The patrol task fails when DataRanger exits with code 2 (an incident needs a person) and succeeds
otherwise. Each run keeps its reports in its own folder, ``<report dir>/<run date>/``, so the
``dashboard.html`` for any day can be opened later.

It patrols the demo data from ``examples/scheduled/make_demo_db.py``, so it needs no credentials. To
patrol your own tables, delete the ``make_demo_db`` task and change the arguments of ``dataranger patrol``.
"""
from __future__ import annotations

import os
from pathlib import Path

import pendulum

try:  # Airflow 3
    from airflow.providers.standard.operators.bash import BashOperator
    from airflow.sdk import DAG
except ImportError:  # Airflow 2
    from airflow import DAG
    from airflow.operators.bash import BashOperator

# Where the run folders go. Set DATARANGER_REPORT_DIR to keep them somewhere else.
REPORT_DIR = os.environ.get("DATARANGER_REPORT_DIR", str(Path.home() / "dataranger-reports"))
DEMO_DB = f"{REPORT_DIR}/demo.duckdb"
MAKE_DEMO_DB = Path(__file__).resolve().parents[1] / "scheduled" / "make_demo_db.py"

with DAG(
    dag_id="dataranger_patrol",
    description="Patrol the tables once a day and fail when an incident needs a person.",
    schedule="17 6 * * *",  # every day at 06:17 UTC; move it to after your nightly load
    start_date=pendulum.datetime(2026, 11, 1, tz="UTC"),
    catchup=False,
    default_args={"retries": 0},
    tags=["dataranger"],
) as dag:
    # Demo only. The helper refuses to overwrite a database, so remove the last one first.
    make_demo_db = BashOperator(
        task_id="make_demo_db",
        bash_command=f'mkdir -p "{REPORT_DIR}" && rm -f "{DEMO_DB}" && python "{MAKE_DEMO_DB}" "{DEMO_DB}"',
    )

    # Exit code 2 means an incident needs a person, and Airflow fails the task on any non-zero exit.
    # --json prints one JSON summary to the task log.
    patrol = BashOperator(
        task_id="patrol",
        bash_command=(
            f'dataranger patrol --db "{DEMO_DB}" '
            "--table transactions --ref-table customers --manifest-table control_totals "
            f'--category category --out "{REPORT_DIR}/{{{{ ds }}}}" --json --fail-on-escalate'
        ),
    )

    make_demo_db >> patrol
