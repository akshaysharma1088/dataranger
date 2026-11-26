"""Writing a proven fix to a clone (issue #97). Snowflake is stood in for by DuckDB, as in test_connectors."""
from datetime import date

import duckdb
import pandas as pd
import pytest

from dataranger.connectors import snowflake_apply as sa
from dataranger.patrol import Mapping

MAPPING = Mapping(key="payment_id", reference="customer_id", date="booking_date", segment="channel",
                  category=None, amount="amount")
D1, D2, D3 = date(2026, 11, 1), date(2026, 11, 2), date(2026, 11, 3)
COLS = ["payment_id", "customer_id", "booking_date", "channel", "amount"]


def _rows():
    """Three days. D1 has an exact duplicate (p2) and copies that disagree on amount (p3); D2 has amounts
    100 times too large; D3 is clean. `note` is a column the patrol never reads."""
    return [
        ("p1", "c1", D1, "card", 10.00, "first"), ("p2", "c1", D1, "card", 20.00, "dup"),
        ("p2", "c1", D1, "card", 20.00, "dup"), ("p3", "c2", D1, "ach", 30.00, "old copy"),
        ("p3", "c2", D1, "ach", 35.00, "new copy"), ("p4", "c9", D1, "ach", 5.00, "orphan"),
        ("p5", "c1", D2, "card", 1000.00, "cents"), ("p6", "c2", D2, "card", 2500.00, "cents too"),
        ("p7", "c1", D3, "card", 7.00, "clean"),
    ]


class Cursor:
    def __init__(self, fake):
        self.fake, self.result, self.sfqid = fake, None, None

    def execute(self, query):
        self.fake.queries.append(query)
        if self.fake.fail_on and self.fake.fail_on in query:
            raise RuntimeError("002003 (42S02): SQL compilation error\nmore detail")
        head, sep, source = query.partition(" clone ")  # DuckDB has no zero-copy clone
        self.result = self.fake.con.execute(f"{head} as select * from {source}" if sep else query)
        self.description = self.result.description
        self.sfqid = f"01-{len(self.fake.queries):04d}"

    def fetchall(self):
        return self.result.fetchall()

    def close(self):
        pass


class Warehouse:
    def __init__(self, rows=None, fail_on=None):
        self.con, self.queries, self.fail_on, self.closed = duckdb.connect(), [], fail_on, False
        self.con.execute("create schema GOLD")
        self.con.execute("create table GOLD.PAYMENTS (payment_id varchar, customer_id varchar, booking_date date, "
                         "channel varchar, amount decimal(18,2), note varchar)")
        self.con.executemany("insert into GOLD.PAYMENTS values (?, ?, ?, ?, ?, ?)", rows or _rows())

    def cursor(self):
        return Cursor(self)

    def close(self):
        self.closed = True

    def table(self, name="GOLD.PAYMENTS"):
        return self.con.execute(f"select * from {name} order by booking_date, payment_id, amount").fetchall()

    def tables(self):
        return sorted(r[0] for r in self.con.execute("select table_name from information_schema.tables").fetchall())


@pytest.fixture
def facts():
    return pd.DataFrame([r[:5] for r in _rows()], columns=COLS)


def _fixed(facts):
    """Keep one p2, keep the 35.00 copy of p3, and divide D2's amounts by 100."""
    out = facts.drop(index=[2, 3]).copy()
    out.loc[out["booking_date"] == D2, "amount"] = out.loc[out["booking_date"] == D2, "amount"] / 100
    return out


def _apply(wh, facts, fixed, days, **kw):
    return sa.write_fix_to_clone("GOLD.PAYMENTS", facts, fixed, MAPPING, days, run_id="t1",
                                 opener=lambda connection, params: wh, **kw)


def test_clone_name_keeps_qualification_and_quotes():
    assert sa.clone_name("DB.GOLD.PAYMENTS", "ab12") == "DB.GOLD.PAYMENTS__DATARANGER_AB12"
    assert sa.clone_name('GOLD."odd name"', "ab12") == 'GOLD."odd name__DATARANGER_AB12"'
    for table, run_id in [("GOLD.PAYMENTS; drop table x", "ab12"), ("GOLD.PAYMENTS", "ab-12"), ("GOLD.PAYMENTS", "")]:
        with pytest.raises(ValueError):
            sa.clone_name(table, run_id)


def test_one_day_fix_lands_on_the_clone_only(facts):
    wh, before = Warehouse(), Warehouse().table()
    fixed = facts.drop(index=[2, 3])
    done = _apply(wh, facts, fixed, [D1])
    assert wh.closed and done["clone"] == "GOLD.PAYMENTS__DATARANGER_T1" and done["days"] == ["2026-11-01"]
    assert done["duplicates_resolved"] == 2 and done["rows_deleted"] == 0 and done["amounts_updated"] == 0
    assert wh.table() == before  # the patrolled table is untouched
    clone = wh.table(done["clone"])
    assert [r for r in clone if r[2] == D1] == [
        ("p1", "c1", D1, "card", 10, "first"), ("p2", "c1", D1, "card", 20, "dup"),
        ("p3", "c2", D1, "ach", 35, "new copy"),  # the unread column came along with the copy that was kept
        ("p4", "c9", D1, "ach", 5, "orphan")]
    assert [r for r in clone if r[2] != D1] == [r for r in before if r[2] != D1]  # other days as cloned
    assert wh.tables() == ["PAYMENTS", "PAYMENTS__DATARANGER_T1"]  # the scratch table is gone
    assert all(s["query_id"] for s in done["statements"])


def test_multi_day_fix_with_deletes_and_amount_changes(facts):
    wh = Warehouse()
    fixed = _fixed(facts)
    fixed = fixed[fixed["payment_id"] != "p4"]  # and quarantine the orphan
    done = _apply(wh, facts, fixed, [D2, D1, D1])
    assert done["days"] == ["2026-11-01", "2026-11-02"]
    assert (done["rows_deleted"], done["duplicates_resolved"], done["amounts_updated"]) == (1, 2, 2)
    clone = wh.table(done["clone"])
    assert [(r[0], float(r[4]), r[5]) for r in clone] == [
        ("p1", 10.0, "first"), ("p2", 20.0, "dup"), ("p3", 35.0, "new copy"),
        ("p5", 10.0, "cents"), ("p6", 25.0, "cents too"), ("p7", 7.0, "clean")]


def test_dedupe_then_rescale_on_one_key():
    rows = [("p1", "c1", D1, "card", 1000.00, "a"), ("p1", "c1", D1, "card", 1000.00, "a")]
    facts = pd.DataFrame([r[:5] for r in rows], columns=COLS)
    fixed = facts.iloc[:1].assign(amount=10.0)
    wh = Warehouse(rows)
    done = _apply(wh, facts, fixed, [D1])
    assert [(r[0], float(r[4])) for r in wh.table(done["clone"])] == [("p1", 10.0)]


def test_no_statement_writes_to_the_patrolled_table(facts):
    wh = Warehouse()
    done = _apply(wh, facts, _fixed(facts), [D1, D2])
    clone, keep = done["clone"], done["clone"] + "_KEEP"
    for q in wh.queries:
        target = q.split()[2] if q.startswith(("delete from", "insert into")) else q.split()[1] if q.startswith("update") else None
        assert target in (None, clone, keep), q
    creates = [q for q in wh.queries if q.startswith("create")]
    assert creates[0] == f"create table {clone} clone GOLD.PAYMENTS" and all(clone in q for q in creates)
    assert not any(q.startswith(("drop", "alter", "truncate")) and q.split()[-1] == "GOLD.PAYMENTS" for q in wh.queries)


def test_a_failed_statement_drops_the_clone_and_reports_one_line(facts):
    wh, before = Warehouse(fail_on="update "), Warehouse().table()
    with pytest.raises(ValueError, match=r"^Snowflake statement failed, the clone was dropped: 002003 \(42S02\): SQL compilation error$"):
        _apply(wh, facts, _fixed(facts), [D1, D2])
    assert wh.closed and wh.tables() == ["PAYMENTS"] and wh.table() == before
    assert "rollback" in wh.queries


def test_copies_that_differ_only_in_an_unread_column_are_refused(facts):
    rows = _rows()
    rows[2] = ("p2", "c1", D1, "card", 20.00, "a different note")
    wh = Warehouse(rows)
    with pytest.raises(ValueError, match="copies differ in a column the patrol did not read"):
        _apply(wh, facts, facts.drop(index=[2, 3]), [D1])
    assert wh.tables() == ["PAYMENTS"]


def test_a_table_that_changed_since_the_patrol_is_refused(facts):
    rows = _rows() + [("p8", "c1", D2, "card", 4.00, "arrived later")]
    wh = Warehouse(rows)
    with pytest.raises(ValueError, match="the table changed since the patrol"):
        _apply(wh, facts, _fixed(facts), [D2])
    assert wh.tables() == ["PAYMENTS"]


def test_fixes_that_cannot_be_replayed_are_refused_before_connecting(facts):
    def refuse(connection, params):
        raise AssertionError("should not connect")

    reload = pd.concat([facts, pd.DataFrame([("p9", "c1", D3, "card", 1.0)], columns=COLS)], ignore_index=True)
    remap = facts.drop(index=[2, 3]).assign(channel="wire")
    still_dup = facts.drop(index=[3])
    for fixed, day, why in [(reload, D3, "adds 1 row"), (remap, D1, "changes a column other than amount"),
                            (still_dup, D1, "still has duplicate")]:
        with pytest.raises(ValueError, match=why):
            sa.write_fix_to_clone("GOLD.PAYMENTS", facts, fixed, MAPPING, [day], run_id="t1", opener=refuse)
    ambiguous = facts.drop(index=[2, 3]).copy()
    ambiguous.loc[ambiguous["payment_id"] == "p3", "amount"] = 0.33
    with pytest.raises(ValueError, match="cannot tell which copy"):
        sa.write_fix_to_clone("GOLD.PAYMENTS", facts, ambiguous, MAPPING, [D1], run_id="t1", opener=refuse)


def test_nothing_to_do_still_gives_a_clone(facts):
    wh = Warehouse()
    done = _apply(wh, facts, facts[facts["booking_date"] == D3], [D3])
    assert done["days"] == [] and wh.table(done["clone"]) == wh.table()


def test_literals_are_quoted_or_refused():
    assert sa._lit("o'brien") == "'o''brien'" and sa._lit(7) == "7" and sa._lit(1.5) == "1.50"
    assert sa._lit(D1) == "'2026-11-01'::date"
    for bad in (None, float("nan"), True, object()):
        with pytest.raises(ValueError):
            sa._lit(bad)


def test_a_real_patrol_replays_onto_the_clone():
    """End to end: the clone ends up equal, on the columns the patrol read, to the patrol's own fixed table."""
    from datetime import timedelta

    from dataranger.bench import generate, inject
    from dataranger.patrol import patrol

    case = inject(generate(7, "small", start=date.today() - timedelta(days=60)))
    mapping = Mapping(category="category")
    t, c = case.warehouse["transactions"], case.warehouse["customers"]  # noqa: F841
    wh = Warehouse.__new__(Warehouse)
    wh.con, wh.queries, wh.fail_on, wh.closed = duckdb.connect(), [], None, False
    wh.con.execute("create schema GOLD")
    wh.con.execute("create table GOLD.PAYMENTS as select txn_id, customer_id, txn_date, channel, category, "
                   "amount::decimal(18,2) as amount, payment_channel from t")
    cols = ["txn_id", "customer_id", "txn_date", "channel", "category", "amount"]
    facts = wh.con.execute(f"select {', '.join(cols)} from GOLD.PAYMENTS").df()
    facts["amount"] = facts["amount"].astype(float)
    res = patrol(facts, c, case.manifest, mapping)
    days = [r["partition"] for r in res.incidents if r["decision"] == "auto-fixed"]
    assert days
    done = sa.write_fix_to_clone("GOLD.PAYMENTS", facts, res.fixed_table, mapping, days, run_id="e2e",
                                 opener=lambda connection, params: wh)
    assert done["days"]
    order = "order by txn_date, txn_id"
    clone = wh.con.execute(f"select {', '.join(cols)} from {done['clone']} {order}").df()
    want = res.fixed_table[cols].sort_values(["txn_date", "txn_id"], ignore_index=True)
    assert len(clone) == len(want) and list(clone["txn_id"]) == list(want["txn_id"])
    assert (clone["amount"].astype(float) - want["amount"]).abs().max() < 0.005


# ---- verify_clone (issue #98): the fixed days, read back from the clone, held to the control totals ----

from datetime import timedelta  # noqa: E402

from dataranger.bench import generate, inject  # noqa: E402
from dataranger.patrol import DEFAULT_LAMBDA, patrol  # noqa: E402
from dataranger.repair.verify import hash_total  # noqa: E402

FACT_COLS = ["txn_id", "customer_id", "txn_date", "channel", "category", "amount"]
BENCH = Mapping(category="category")


class Patrolled:
    """A patrolled warehouse table, its control totals, the patrol's result and the clone carrying its fixes."""

    def __init__(self, hash_totals=False, upper=False):
        case = inject(generate(7, "small", start=date.today() - timedelta(days=60)))
        t, man = case.warehouse["transactions"], case.manifest.copy()  # noqa: F841
        if hash_totals:  # what a source that publishes a hash total per day would send
            src = case.source["transactions"]
            totals = {d: hash_total(g) for d, g in src.groupby("txn_date")}
            man["hash_total"] = [totals.get(d) if ch == "*" else None for d, ch in zip(man["txn_date"], man["channel"])]
        wh = Warehouse.__new__(Warehouse)
        wh.con, wh.queries, wh.fail_on, wh.closed = duckdb.connect(), [], None, False
        wh.con.execute("create schema GOLD; create schema OPS")
        q = (lambda c: f'{c} as "{c.upper()}"') if upper else (lambda c: c)
        amount = 'amount::decimal(18,2) as "AMOUNT"' if upper else "amount::decimal(18,2) as amount"
        wh.con.execute(f"create table GOLD.PAYMENTS as select {q('txn_id')}, {q('customer_id')}, {q('txn_date')}, "
                       f"{q('channel')}, {q('category')}, {amount}, {q('payment_channel')} from t")
        wh.con.execute("create table OPS.CONTROL_TOTALS as select "
                       + ", ".join(q(c) for c in man.columns) + " from man")
        self.wh, self.open = wh, lambda connection, params: wh
        self.facts = wh.con.execute(f"select {', '.join(FACT_COLS)} from GOLD.PAYMENTS").df()
        self.facts.columns = FACT_COLS
        self.facts["amount"] = self.facts["amount"].astype(float)
        self.res = patrol(self.facts, case.warehouse["customers"], man, BENCH)
        self.fixed_days = [r["partition"] for r in self.res.incidents if r["decision"] == "auto-fixed"]
        self.residuals = {str(r["partition"]): r["chosen"]["residual"] for r in self.res.incidents
                          if r["decision"] == "auto-fixed"}
        done = sa.write_fix_to_clone("GOLD.PAYMENTS", self.facts, self.res.fixed_table, BENCH, self.fixed_days,
                                     run_id="v1", opener=self.open)
        self.clone, self.days = done["clone"], done["days"]
        wh.queries.clear()

    def verify(self, **kw):
        kw.setdefault("lam", DEFAULT_LAMBDA)
        return sa.verify_clone(self.clone, "OPS.CONTROL_TOTALS", BENCH, self.days, opener=self.open, **kw)


@pytest.fixture
def patrolled():
    return Patrolled()


def test_a_correctly_written_clone_passes_with_the_gates_own_gap(patrolled):
    out = patrolled.verify(fixed=patrolled.res.fixed_table)
    assert out["passed"] and not out["dropped"] and [r["day"] for r in out["days"]] == patrolled.days
    for r in out["days"]:  # the same number the gate saw when it approved the fix
        assert r["passed"] and r["reason"] is None and r["threshold"] == DEFAULT_LAMBDA
        assert r["gap"] == pytest.approx(patrolled.residuals[r["day"]], abs=1e-6)
    assert patrolled.clone.split(".")[-1] in patrolled.wh.tables()
    assert len(patrolled.wh.queries) == 2 and all(q.startswith("select ") for q in patrolled.wh.queries)
    assert patrolled.wh.closed


def test_a_truncated_amount_fails_and_the_clone_is_dropped(patrolled):
    day = patrolled.days[0]
    before = patrolled.wh.con.execute("select count(*), sum(amount) from GOLD.PAYMENTS").fetchall()
    patrolled.wh.con.execute(f"update {patrolled.clone} set amount = floor(amount / 10) where txn_date = '{day}'")
    out = patrolled.verify()
    bad = {r["day"]: r for r in out["days"]}[day]
    assert not out["passed"] and out["dropped"] and not bad["passed"] and "above the threshold" in bad["reason"]
    assert all(r["passed"] for r in out["days"] if r["day"] != day)
    assert patrolled.clone.split(".")[-1] not in patrolled.wh.tables()
    assert patrolled.wh.queries[-1] == f"drop table if exists {patrolled.clone}"
    assert patrolled.wh.con.execute("select count(*), sum(amount) from GOLD.PAYMENTS").fetchall() == before


def _swap_two_amounts(p, day):
    """Swap the amounts of two rows in one segment of the clone: the day's counts and sums do not move."""
    a, b = p.wh.con.execute(f"select txn_id, amount from {p.clone} where txn_date = '{day}' and channel = "
                            f"(select min(channel) from {p.clone} where txn_date = '{day}') "
                            "order by amount limit 2 offset 3").fetchall()
    assert a[1] != b[1]
    for key, amount in ((a[0], b[1]), (b[0], a[1])):
        p.wh.con.execute(f"update {p.clone} set amount = {amount} where txn_id = '{key}' and txn_date = '{day}'")


def test_swapped_amounts_pass_on_totals_alone_but_not_against_the_approved_fix(patrolled):
    day = patrolled.days[0]
    _swap_two_amounts(patrolled, day)
    assert patrolled.verify(drop_on_fail=False)["passed"]  # counts and sums cannot see a swap
    out = patrolled.verify(fixed=patrolled.res.fixed_table, drop_on_fail=False)
    bad = {r["day"]: r for r in out["days"]}[day]
    assert not out["passed"] and bad["reason"] == "the rows differ from the fix the gate approved"
    assert not out["dropped"] and patrolled.clone.split(".")[-1] in patrolled.wh.tables()


def test_a_published_hash_total_catches_the_swap_without_the_approved_fix():
    """The day's count and amount match the control totals, so its hash total has to match exactly (#113)."""
    p = Patrolled(hash_totals=True)
    day = p.days[0]
    assert p.verify()["passed"]
    _swap_two_amounts(p, day)
    out = p.verify()
    bad = {r["day"]: r for r in out["days"]}[day]
    assert not out["passed"] and out["dropped"] and bad["gap"] == 1.0 and "above the threshold" in bad["reason"]


def test_names_in_upper_case_as_snowflake_returns_them():
    p = Patrolled(hash_totals=True, upper=True)
    out = p.verify(fixed=p.res.fixed_table)
    assert out["passed"] and len(out["days"]) == len(p.days)


def test_a_day_with_no_control_totals_fails(patrolled):
    day = patrolled.days[0]
    patrolled.wh.con.execute(f"delete from OPS.CONTROL_TOTALS where txn_date = '{day}'")
    out = patrolled.verify()
    bad = {r["day"]: r for r in out["days"]}[day]
    assert not out["passed"] and bad["gap"] is None and bad["reason"] == "no control totals for this day"


def test_bad_names_are_refused_before_connecting_and_errors_are_one_line(patrolled):
    def refuse(connection, params):
        raise AssertionError("should not connect")

    for clone, totals, days in [("GOLD.X; drop table y", "OPS.CONTROL_TOTALS", [D1]), ("GOLD.X", "OPS.T--", [D1]),
                                ("GOLD.X", "OPS.CONTROL_TOTALS", [])]:
        with pytest.raises(ValueError):
            sa.verify_clone(clone, totals, BENCH, days, lam=DEFAULT_LAMBDA, opener=refuse)
    patrolled.wh.fail_on = "CONTROL_TOTALS"
    with pytest.raises(ValueError, match=r"^Snowflake query failed: 002003 \(42S02\): SQL compilation error$"):
        patrolled.verify()
    assert patrolled.wh.closed
