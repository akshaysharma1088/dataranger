"""Read the tables behind a dbt BigQuery target. Read-only: three SELECT statements, nothing else.

Needs the BigQuery client: pip install "dataranger[bigquery]"
The jobs are labelled application=dataranger, so they are easy to find in the project's job history.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

INSTALL = 'The dbt BigQuery target needs the BigQuery client: pip install "dataranger[bigquery]"'
METHODS = ("oauth", "service-account", "service-account-json")


def params_from_profile(output: dict) -> dict:
    """Client settings for a dbt-bigquery output: auth method, billing project, location and key file."""
    method = output.get("method") or "oauth"
    if method not in METHODS:
        raise ValueError(f"dbt BigQuery auth method {method!r} is not supported yet ({', '.join(METHODS)} are)")
    project = output.get("execution_project") or output.get("project") or output.get("database")
    params = {"method": method, "project": project, "location": output.get("location")}
    if method == "service-account":
        keyfile = output.get("keyfile")
        if not keyfile:
            raise ValueError("dbt BigQuery method service-account needs keyfile in the profile")
        path = Path(keyfile).expanduser()
        if not path.is_absolute():
            path = Path(output.get("_project_dir", ".")) / path
        params["keyfile"] = str(path)
    elif method == "service-account-json":
        if not isinstance(output.get("keyfile_json"), dict):
            raise ValueError("dbt BigQuery method service-account-json needs keyfile_json in the profile")
        params["keyfile_json"] = output["keyfile_json"]
    return {k: v for k, v in params.items() if v not in (None, "")}


def connect(params: dict):
    """A BigQuery client: application default credentials (oauth) or the profile's service account key."""
    try:
        from google.cloud import bigquery
    except ImportError:
        raise SystemExit(INSTALL) from None
    params = dict(params)
    method = params.pop("method", "oauth")
    kwargs = {k: params[k] for k in ("project", "location") if params.get(k)}
    if method == "service-account":
        return bigquery.Client.from_service_account_json(params["keyfile"], **kwargs)
    if method == "service-account-json":
        return bigquery.Client.from_service_account_info(params["keyfile_json"], **kwargs)
    return bigquery.Client(**kwargs)


def _frame(client, query: str) -> pd.DataFrame:
    job_config = None
    try:
        from google.cloud import bigquery

        job_config = bigquery.QueryJobConfig(labels={"application": "dataranger"})
    except ImportError:  # a test's stand-in client
        pass
    rows = client.query(query, job_config=job_config).result()
    columns = [field.name for field in rows.schema]
    return pd.DataFrame([tuple(row.values()) for row in rows], columns=columns)


def run_queries(queries, params: dict, opener=None) -> tuple:
    """Run the read-only queries with one client. BigQuery errors come back as one readable line."""
    try:
        client = (opener or connect)(params)
    except SystemExit:
        raise
    except Exception as e:
        raise ValueError(f"could not connect to BigQuery: {_first_line(e)}") from None
    try:
        return tuple(_frame(client, q) for q in queries)
    except Exception as e:
        raise ValueError(f"BigQuery query failed: {_first_line(e)}") from None
    finally:
        close = getattr(client, "close", None)
        if close:
            close()


def _first_line(e: Exception) -> str:
    return (str(e).strip().splitlines() or [type(e).__name__])[0]
