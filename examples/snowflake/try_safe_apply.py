"""Try the first two steps of safe apply on the Snowflake demo data.

Run `load_demo.py` first. This script then:
  1. patrols DATARANGER_DEMO.PUBLIC.PAYMENTS (three SELECT statements),
  2. clones the table and replays the proven fixes on the clone,
  3. reads the fixed days back from the clone and holds them to the control totals,
  4. checks that the patrolled table itself did not change.

The patrolled table is never written to. The clone is left in place so you can look at it;
the last line printed is the statement that removes it.

Uses the same SNOWFLAKE_* environment variables as `dataranger patrol --snowflake`. One session
is opened and shared by every step, so a multi-factor login needs one code, asked for at the start.
"""
import os

from dataranger.connectors import snowflake_apply as sa
from dataranger.connectors.snowflake import connect, from_snowflake
from dataranger.patrol import Mapping, patrol

TABLE = "DATARANGER_DEMO.PUBLIC.PAYMENTS"
REFS = "DATARANGER_DEMO.PUBLIC.CUSTOMERS"
TOTALS = "DATARANGER_DEMO.PUBLIC.CONTROL_TOTALS"
DAYS = 45


class Shared:
    """One open session handed to each step; the steps' own close() calls are ignored."""

    def __init__(self, conn):
        self.conn = conn

    def cursor(self):
        return self.conn.cursor()

    def close(self):
        pass


def size(conn, table):
    cur = conn.cursor()
    try:
        cur.execute(f"select count(*), coalesce(sum(amount), 0) from {table}")
        n, total = cur.fetchall()[0]
        return int(n), round(float(total), 2)
    finally:
        cur.close()


def run(conn, table=TABLE, refs=REFS, totals=TOTALS, days=DAYS, mapping=None, run_id=None):
    mapping = mapping or Mapping(category="category")
    opener = lambda connection, params: Shared(conn)  # noqa: E731
    before = size(conn, table)

    facts, ref_rows, manifest = from_snowflake(table, refs, totals, mapping, days=days, opener=opener)
    res = patrol(facts, ref_rows, manifest, mapping)
    fixed_days = [r["partition"] for r in res.incidents if r["decision"] == "auto-fixed"]
    print(f"Patrol: {len(res.incidents)} incidents, {len(fixed_days)} with a proven fix: "
          + ", ".join(str(d)[:10] for d in fixed_days))
    if not fixed_days:
        print("Nothing to write to a clone.")
        return None

    done = sa.write_fix_to_clone(table, facts, res.fixed_table, mapping, fixed_days, run_id=run_id, opener=opener)
    print(f"Clone: {done['clone']}  ({done['rows_deleted']} rows deleted, {done['duplicates_resolved']} "
          f"duplicates resolved, {done['amounts_updated']} amounts updated, {len(done['statements'])} statements)")

    check = sa.verify_clone(done["clone"], totals, mapping, done["days"], lam=res.lam, fixed=res.fixed_table,
                            drop_on_fail=False, opener=opener)
    for day in check["days"]:
        verdict = "pass" if day["passed"] else f"FAIL: {day['reason']}"
        print(f"  {day['day']}: {day['rows']} rows, gap {day['gap']:.4f} (threshold {day['threshold']:.4f})  {verdict}")
    print("Clone check:", "PASSED" if check["passed"] else "FAILED")

    after = size(conn, table)
    print(f"Patrolled table: {before[0]:,} rows totalling {before[1]:,.2f} before, "
          f"{after[0]:,} rows totalling {after[1]:,.2f} after:", "unchanged" if before == after else "CHANGED")
    print(f"Clone table:     {size(conn, done['clone'])[0]:,} rows")
    print(f"To remove the clone:  drop table {done['clone']};")
    return {"write": done, "check": check, "unchanged": before == after}


def main():
    if os.environ.get("SNOWFLAKE_AUTHENTICATOR", "").lower() == "username_password_mfa":
        os.environ["SNOWFLAKE_PASSCODE"] = input("Code from your authenticator app: ").strip()
    conn = connect()
    try:
        run(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
