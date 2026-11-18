"""Read the tables behind a dbt Postgres target. Read-only: three SELECT statements, nothing else.

Needs a Postgres driver: pip install "dataranger[postgres]" (psycopg 3; psycopg2 works too).
The session is opened with default_transaction_read_only=on and application_name=dataranger.
"""
from __future__ import annotations

import pandas as pd

INSTALL = 'The dbt Postgres target needs a Postgres driver: pip install "dataranger[postgres]"'

# dbt-postgres profile key -> libpq connection argument
PROFILE_KEYS = {
    "host": "host", "port": "port", "user": "user", "password": "password", "pass": "password",
    "dbname": "dbname", "database": "dbname", "sslmode": "sslmode", "sslcert": "sslcert", "sslkey": "sslkey",
    "sslrootcert": "sslrootcert", "connect_timeout": "connect_timeout", "keepalives_idle": "keepalives_idle",
}


def params_from_profile(output: dict) -> dict:
    """Connection arguments for a dbt-postgres output. `role` is kept so the session can SET ROLE to it."""
    params = {arg: output[key] for key, arg in PROFILE_KEYS.items() if output.get(key) not in (None, "")}
    if output.get("role"):
        params["role"] = output["role"]
    return params


def _quote(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def connect(params: dict):
    """Open a read-only session with psycopg 3, or psycopg2 when that is what is installed."""
    try:
        import psycopg as driver
    except ImportError:
        try:
            import psycopg2 as driver
        except ImportError:
            raise SystemExit(INSTALL) from None
    params = dict(params)
    role = params.pop("role", None)
    params.setdefault("application_name", "dataranger")
    params["options"] = "-c default_transaction_read_only=on"
    conn = driver.connect(**params)
    if role:
        cur = conn.cursor()
        try:
            cur.execute(f"set role {_quote(role)}")
        finally:
            cur.close()
    return conn


def _frame(conn, query: str) -> pd.DataFrame:
    cur = conn.cursor()
    try:
        cur.execute(query)
        columns = [getattr(d, "name", None) or d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=columns)
    finally:
        cur.close()


def run_queries(queries, params: dict, opener=None) -> tuple:
    """Open a session, run the read-only queries and close it. Postgres errors come back as one readable line."""
    try:
        conn = (opener or connect)(params)
    except SystemExit:
        raise
    except Exception as e:
        raise ValueError(f"could not connect to Postgres: {_first_line(e)}") from None
    try:
        return tuple(_frame(conn, q) for q in queries)
    except Exception as e:
        raise ValueError(f"Postgres query failed: {_first_line(e)}") from None
    finally:
        conn.close()


def _first_line(e: Exception) -> str:
    return (str(e).strip().splitlines() or [type(e).__name__])[0]
