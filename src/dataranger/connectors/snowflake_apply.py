"""Safe apply on Snowflake, the parts before the swap (issues #97 and #98, part of #96).

Nothing here writes to the table that was patrolled. `write_fix_to_clone` puts the fix on a clone
named ``<table>__DATARANGER_<run id>``. `verify_clone` then reads the fixed days back from that clone
and holds them to the source's control totals with the same rule the patrol's gate uses. Swapping
the clone in (#99) is a separate step. A patrol stays read-only unless this module is called.

A patrol reads only the mapped columns, so the fixed rows it holds are not whole rows. The fix is
therefore replayed on the clone as row-level statements (delete, keep one copy, update the amount),
which leaves every column the patrol never read exactly as it was cloned.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from ..repair.verify import residual, table_fingerprint
from . import sql
from .snowflake import _first_line, _frame, connect

CHUNK = 1000  # rows per statement, well under Snowflake's limit on the length of an IN list
_RUN_ID = re.compile(r"[A-Za-z0-9_]+")


@dataclass
class PartitionPlan:
    """The row-level changes that turn one day of the table into its fixed version."""
    day: date
    delete_keys: list = field(default_factory=list)   # keys the fix removes altogether
    keep_copies: list = field(default_factory=list)   # (key, amount): the one copy of a duplicated key to keep
    set_amounts: list = field(default_factory=list)   # (key, amount): rows whose amount the fix changes
    rows: int = 0                                     # rows the day must have afterwards
    amount: float = 0.0                               # and their total

    @property
    def empty(self) -> bool:
        return not (self.delete_keys or self.keep_copies or self.set_amounts)


def clone_name(table: str, run_id: str) -> str:
    """``DB.SCHEMA.PAYMENTS`` -> ``DB.SCHEMA.PAYMENTS__DATARANGER_<RUN ID>``, keeping any quoting."""
    sql.relation(table, "fact table")
    if not _RUN_ID.fullmatch(run_id or ""):
        raise ValueError(f"run id {run_id!r} is not valid: use letters, digits and underscores")
    suffix = f"__DATARANGER_{run_id.upper()}"
    return f'{table[:-1]}{suffix}"' if table.endswith('"') else table + suffix


def plan_partition(before: pd.DataFrame, after: pd.DataFrame, mapping, day: date) -> PartitionPlan:
    """Compare one day before and after the fix (both with the user's column names).

    Raises ValueError when the fix cannot be replayed row by row: it adds rows (a reload from the
    source), leaves a key duplicated, changes a column other than the amount, or keeps one of
    several copies that the mapped columns cannot tell apart from the one it dropped.
    """
    k, a = mapping.key, mapping.amount
    other = [c for c in (mapping.reference, mapping.segment, mapping.category) if c and c in before and c in after]
    if after[k].duplicated().any():
        raise ValueError(f"{day}: the fixed version still has duplicate {k} values")
    added = set(after[k]) - set(before[k])
    if added:
        raise ValueError(f"{day}: the fix adds {len(added)} row(s) that are not in the table (a reload from the "
                         "source); that cannot be written to a clone yet")
    plan = PartitionPlan(day, rows=len(after), amount=round(float(after[a].sum()), 2))
    plan.delete_keys = sorted(set(before[k]) - set(after[k]), key=str)
    copies = {key: grp for key, grp in before.groupby(k, sort=False)}
    for row in after.itertuples(index=False):
        key, new = getattr(row, k), round(float(getattr(row, a)), 2)
        old = copies[key]
        if any(set(old[c].astype(str)) != {str(getattr(row, c))} for c in other):
            raise ValueError(f"{day}: the fix changes a column other than {a} for {k} {key!r}; "
                             "only amount changes can be written to a clone yet")
        amounts = sorted({round(float(x), 2) for x in old[a]})
        if len(old) > 1:
            kept = new if new in amounts else (amounts[0] if len(amounts) == 1 else None)
            if kept is None:
                raise ValueError(f"{day}: cannot tell which copy of {k} {key!r} the fix kept")
            plan.keep_copies.append((key, kept))
            if kept != new:
                plan.set_amounts.append((key, new))
        elif amounts[0] != new:
            plan.set_amounts.append((key, new))
    return plan


def _lit(value) -> str:
    """A SQL literal for a key, an amount or a date. Anything else is refused, not guessed at."""
    if isinstance(value, bool):
        raise ValueError(f"cannot write {value!r} as a key")
    if hasattr(value, "item"):  # numpy scalars
        value = value.item()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("cannot write a missing or infinite number")
        return f"{value:.2f}"
    if isinstance(value, date):
        return f"'{value.isoformat()}'::date"
    if isinstance(value, str):
        return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"
    raise ValueError(f"cannot write a value of type {type(value).__name__} as a key")


def _chunks(items: list, size: int = CHUNK):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def statements(plan: PartitionPlan, clone: str, mapping, keep_table: str) -> tuple[list[str], list[str]]:
    """(statements to run before the transaction, statements to run inside it) for one day.

    The copies to keep are set aside first, in a scratch table next to the clone, because Snowflake
    commits an open transaction on any CREATE. Every statement names the clone or the scratch table,
    never the table that was patrolled.
    """
    k, a, d = sql.column(mapping.key), sql.column(mapping.amount), sql.column(mapping.date, "date column")
    day = f"{d} = {_lit(plan.day)}"
    before, inside = [], []
    for part in _chunks(plan.delete_keys):
        inside.append(f"delete from {clone} where {day} and {k} in ({', '.join(_lit(x) for x in part)})")
    if plan.keep_copies:
        before.append(f"create or replace table {keep_table} as select * from {clone} where 1 = 0")
        for part in _chunks(plan.keep_copies):
            pairs = ", ".join(f"({_lit(key)}, {_lit(amt)})" for key, amt in part)
            before.append(f"insert into {keep_table} select distinct * from {clone} where {day} and ({k}, {a}) in ({pairs})")
        for part in _chunks([key for key, _ in plan.keep_copies]):
            inside.append(f"delete from {clone} where {day} and {k} in ({', '.join(_lit(x) for x in part)})")
        inside.append(f"insert into {clone} select * from {keep_table}")
    for part in _chunks(plan.set_amounts):
        values = ", ".join(f"({_lit(key)}, {_lit(amt)})" for key, amt in part)
        inside.append(f"update {clone} set {a} = v.new_amount from (values {values}) as v (fix_key, new_amount) "
                      f"where {clone}.{k} = v.fix_key and {clone}.{day}")
    return before, inside


def write_fix_to_clone(table: str, facts: pd.DataFrame, fixed: pd.DataFrame, mapping, days, run_id: str | None = None,
                       connection: str | None = None, params: dict | None = None, opener=None) -> dict:
    """Clone `table` and replay the fix for `days` on the clone. Returns what was done, for the audit log.

    `facts` is what the patrol read and `fixed` is `PatrolResult.fixed_table`; `days` are the dates the
    gate chose to fix. Other days are not touched. If anything fails, the clone is dropped and the
    error is raised as one readable line; the patrolled table is not written to in any case.
    """
    run_id = run_id or uuid.uuid4().hex[:8]
    clone = clone_name(table, run_id)
    keep_table = clone_name(table, run_id + "_KEEP")  # a scratch table next to the clone, dropped below
    date_col = mapping.date
    on_day = lambda df, day: df[pd.to_datetime(df[date_col]).dt.date == day]
    plans = []
    for day in sorted({pd.Timestamp(x).date() for x in days}):  # plan everything before connecting
        plan = plan_partition(on_day(facts, day), on_day(fixed, day), mapping, day)
        if not plan.empty:
            plans.append(plan)
    try:
        conn = (opener or connect)(connection, params)
    except SystemExit:
        raise
    except Exception as e:
        raise ValueError(f"could not connect to Snowflake: {_first_line(e)}") from None
    log: list[dict] = []
    cur = conn.cursor()

    def run(statement: str):
        cur.execute(statement)
        log.append({"statement": statement.split(" where ")[0].split(" select ")[0][:120],
                    "query_id": getattr(cur, "sfqid", None)})

    created = False
    try:
        run(f"create table {clone} clone {table}")
        created = True
        for plan in plans:
            before, inside = statements(plan, clone, mapping, keep_table)
            for s in before:
                run(s)
            if plan.keep_copies:
                cur.execute(f"select count(*) from {keep_table}")
                found = int(cur.fetchall()[0][0])
                if found != len(plan.keep_copies):
                    raise ValueError(f"{plan.day}: expected one copy to keep for each of {len(plan.keep_copies)} "
                                     f"duplicated keys but found {found}; the copies differ in a column the patrol "
                                     "did not read, or the table changed since the patrol")
            run("begin")
            try:
                for s in inside:
                    run(s)
                cur.execute(f"select count(*), coalesce(sum({sql.column(mapping.amount)}), 0) from {clone} "
                            f"where {sql.column(date_col, 'date column')} = {_lit(plan.day)}")
                n, total = cur.fetchall()[0]
                if int(n) != plan.rows or abs(float(total) - plan.amount) > 0.005:
                    raise ValueError(f"{plan.day}: the clone has {int(n)} rows totalling {float(total):.2f} after the "
                                     f"fix, expected {plan.rows} rows totalling {plan.amount:.2f}; the table changed "
                                     "since the patrol")
            except Exception:
                cur.execute("rollback")
                raise
            run("commit")
            if plan.keep_copies:
                run(f"drop table if exists {keep_table}")
    except Exception as e:
        for leftover in ((keep_table, clone) if created else (keep_table,)):
            try:
                cur.execute(f"drop table if exists {leftover}")
            except Exception:  # noqa: S110 - the first error is the one to report
                pass
        if isinstance(e, ValueError):
            raise
        raise ValueError(f"Snowflake statement failed, the clone was dropped: {_first_line(e)}") from None
    finally:
        cur.close()
        conn.close()
    return {"clone": clone, "table": table, "run_id": run_id, "days": [p.day.isoformat() for p in plans],
            "rows_deleted": sum(len(p.delete_keys) for p in plans),
            "duplicates_resolved": sum(len(p.keep_copies) for p in plans),
            "amounts_updated": sum(len(p.set_amounts) for p in plans), "statements": log}


def _day_list(days) -> list[date]:
    return sorted({pd.Timestamp(x).date() for x in days})


def verify_queries(clone: str, manifest_table: str, mapping, days) -> tuple[str, str]:
    """The two read-only queries of the check: the fixed days as they now sit in the clone, and every
    column of the control totals for those days (so optional measures such as hash_total come along)."""
    wanted = _day_list(days)
    if not wanted:
        raise ValueError("no days to check")
    d = sql.column(mapping.date, "date column")
    cols = [mapping.key, mapping.reference, mapping.date, mapping.segment, mapping.amount]
    if mapping.category:
        cols.append(mapping.category)
    cols = list(dict.fromkeys(sql.column(c) for c in cols))
    where = f" where {d} in ({', '.join(_lit(x) for x in wanted)})"
    return (f"select {', '.join(cols)} from {sql.relation(clone, 'clone')}{where}",
            f"select * from {sql.relation(manifest_table, 'control totals table')}{where}")


def _control_totals(frame: pd.DataFrame, mapping) -> pd.DataFrame:
    """Control totals as `residual` expects them, whatever case the warehouse returned the columns in:
    the same preparation a patrol applies (`sql.normalize`, then `Mapping.totals_to_internal`)."""
    lower = {str(c).lower() for c in frame.columns}
    missing = [c for c in (mapping.date, mapping.segment, "n", "amount") if c.lower() not in lower]
    if missing:
        raise ValueError(f"the control totals table has no column {', '.join(missing)}")
    _, _, totals = sql.normalize(pd.DataFrame(), pd.DataFrame(), frame, mapping)
    return mapping.totals_to_internal(totals)


def verify_clone(clone: str, manifest_table: str, mapping, days, lam: float, fixed: pd.DataFrame | None = None,
                 drop_on_fail: bool = True, connection: str | None = None, params: dict | None = None,
                 opener=None) -> dict:
    """Check the fixed days in `clone` against the source's control totals. Returns one result per day.

    The rows are read back from the clone, so this checks what the write left there, not what the
    patrol meant to write. Each day's gap is computed by `repair.verify.residual`, the same function
    and granularity the gate used, and passes when it is at or below `lam`, the patrol's threshold.
    Optional measures (hash_total, min_amount, max_amount, distinct_<column>) count when the control
    totals carry them. If `fixed` (`PatrolResult.fixed_table`) is given, each day must also hold
    exactly the rows the gate approved.

    A day with no control totals fails. If any day fails and `drop_on_fail` is set, the clone is
    dropped, so there is nothing left to swap in. Connection and query errors raise ValueError.
    """
    from ..patrol import INTERNAL  # here, not at the top: patrol imports the connectors

    wanted = _day_list(days)
    queries = verify_queries(clone, manifest_table, mapping, wanted)  # refuses bad names before connecting
    try:
        conn = (opener or connect)(connection, params)
    except SystemExit:
        raise
    except Exception as e:
        raise ValueError(f"could not connect to Snowflake: {_first_line(e)}") from None
    try:
        try:
            rows, totals = (_frame(conn, q) for q in queries)
        except Exception as e:
            raise ValueError(f"Snowflake query failed: {_first_line(e)}") from None
        cols = [c for c in (mapping.key, mapping.reference, mapping.date, mapping.segment, mapping.amount,
                            mapping.category) if c]
        rows = sql._match_case(rows, cols)
        rows[mapping.amount] = pd.to_numeric(rows[mapping.amount], errors="coerce").astype(float)
        part_all, man = mapping.to_internal(rows), _control_totals(totals, mapping)
        approved = mapping.to_internal(fixed) if fixed is not None else None
        results = []
        for day in wanted:
            part = part_all[part_all["txn_date"] == day]
            gap = residual(part, man, day, "channel")
            reason = None
            if gap == float("inf"):
                reason = "no control totals for this day"
            elif part[list(INTERNAL)].isna().any().any():
                reason = "a mapped column holds a missing value"
            elif gap > lam:
                reason = f"gap {gap:.4f} is above the threshold {lam:.4f}"
            elif approved is not None and (table_fingerprint(part)
                                           != table_fingerprint(approved[approved["txn_date"] == day])):
                reason = "the rows differ from the fix the gate approved"
            results.append({"day": day.isoformat(), "rows": int(len(part)),
                            "amount": round(float(part["amount"].sum()), 2),
                            "gap": None if gap == float("inf") else round(gap, 6), "threshold": lam,
                            "passed": reason is None, "reason": reason})
        passed = all(r["passed"] for r in results)
        dropped = False
        if not passed and drop_on_fail:
            cur = conn.cursor()
            try:
                cur.execute(f"drop table if exists {clone}")
                dropped = True
            except Exception as e:
                raise ValueError(f"the check failed and the clone could not be dropped: {_first_line(e)}") from None
            finally:
                cur.close()
        return {"clone": clone, "passed": passed, "dropped": dropped, "days": results}
    finally:
        conn.close()
