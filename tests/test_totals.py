"""`dataranger totals`: control totals built from the raw or landing table (#62)."""
from datetime import date, timedelta

import duckdb
import pandas as pd
import pytest

from dataranger.bench import generate, inject
from dataranger.cli import main


def _case(p_stale=0.15):
    return inject(generate(7, "small", start=date.today() - timedelta(days=60)), p_stale=p_stale)


def _write(df, path):
    con = duckdb.connect()
    con.register("df", df)
    con.execute(f"copy df to '{path}' (format {'parquet' if str(path).endswith('.parquet') else 'csv, header true'})")
    con.close()


def _manifest(df):
    out = df[["txn_date", "channel", "n", "amount"]].copy()
    out["txn_date"] = pd.to_datetime(out["txn_date"]).dt.date
    return out.sort_values(["txn_date", "channel"], ignore_index=True)


def test_totals_of_the_demo_source_let_patrol_reproduce_the_demo(tmp_path):
    # With exact source totals (p_stale=0) the demo's manifest is exactly what `totals` computes from the
    # source table, so patrol reaches the same decisions with either.
    case = _case(p_stale=0.0)
    _write(case.source["transactions"], tmp_path / "raw.parquet")
    _write(case.warehouse["transactions"], tmp_path / "facts.parquet")
    _write(case.warehouse["customers"], tmp_path / "customers.parquet")
    _write(case.manifest, tmp_path / "demo_totals.csv")

    assert main(["totals", "--facts-file", str(tmp_path / "raw.parquet"), "--out", str(tmp_path / "totals.csv")]) == 0
    built = pd.read_csv(tmp_path / "totals.csv")
    assert list(built.columns) == ["txn_date", "channel", "n", "amount"]
    pd.testing.assert_frame_equal(_manifest(built), _manifest(case.manifest), check_dtype=False)

    for name in ("demo_totals", "totals"):
        assert main(["patrol", "--facts-file", str(tmp_path / "facts.parquet"), "--ref-file", str(tmp_path / "customers.parquet"),
                     "--manifest-file", str(tmp_path / f"{name}.csv"), "--out", str(tmp_path / name)]) == 0
    demo = pd.read_csv(tmp_path / "demo_totals" / "incidents.csv")
    ours = pd.read_csv(tmp_path / "totals" / "incidents.csv")
    assert len(demo) > 0
    pd.testing.assert_frame_equal(ours, demo)


def test_totals_match_the_demo_manifest_on_every_day_it_is_not_stale(tmp_path):
    case = _case()
    _write(case.source["transactions"], tmp_path / "raw.parquet")
    assert main(["totals", "--facts-file", str(tmp_path / "raw.parquet"), "--out", str(tmp_path / "totals.parquet")]) == 0
    built = _manifest(duckdb.connect().execute(f"select * from read_parquet('{tmp_path / 'totals.parquet'}')").df())
    stale = {pd.Timestamp(d).date() for d in case.manifest.loc[case.manifest["stale_rows"] > 0, "txn_date"]}
    assert stale, "the default demo has stale days"
    demo = _manifest(case.manifest)
    keep = lambda df: df[~df["txn_date"].isin(stale)].reset_index(drop=True)  # noqa: E731
    pd.testing.assert_frame_equal(keep(built), keep(demo), check_dtype=False)


def _raw_db(path, rows):
    con = duckdb.connect(str(path))
    con.execute("create schema raw")
    con.execute('create table raw.payments (payment_id int, booked_at timestamp, "pay channel" varchar, value double)')
    con.executemany("insert into raw.payments values (?, ?, ?, ?)", rows)
    con.close()


def test_totals_from_duckdb_with_mapped_columns_into_a_new_schema(tmp_path, capsys):
    db = tmp_path / "warehouse.duckdb"
    _raw_db(db, [(1, "2026-11-01 08:00", "card", 10.0), (2, "2026-11-01 23:59", "card", 5.5),
                 (3, "2026-11-01 12:00", "wire", 100.0), (4, "2026-11-02 00:00", "card", 1.0),
                 (5, "2026-11-02 09:30", None, 2.0)])
    rc = main(["totals", "--db", str(db), "--table", "raw.payments", "--out-table", "ops.payments_totals",
               "--date", "booked_at", "--segment", "pay channel", "--amount", "value"])
    assert rc == 0
    assert "5 control-total rows for 2 day(s) written to ops.payments_totals" in capsys.readouterr().out
    con = duckdb.connect(str(db), read_only=True)
    assert [(c[0], c[1]) for c in con.execute("describe ops.payments_totals").fetchall()] == [
        ("booked_at", "DATE"), ("pay channel", "VARCHAR"), ("n", "BIGINT"), ("amount", "DOUBLE")]
    rows = [(str(r[0]), r[1], r[2], r[3]) for r in con.execute("select * from ops.payments_totals").fetchall()]
    con.close()
    # A timestamp counts toward its calendar day; a row with no segment counts toward the day's '*' only.
    assert rows == [("2026-11-01", "*", 3, 115.5), ("2026-11-01", "card", 2, 15.5), ("2026-11-01", "wire", 1, 100.0),
                    ("2026-11-02", "*", 2, 3.0), ("2026-11-02", "card", 1, 1.0)]
    # Running it again replaces the table rather than appending.
    assert main(["totals", "--db", str(db), "--table", "raw.payments", "--out-table", "ops.payments_totals",
                 "--date", "booked_at", "--segment", "pay channel", "--amount", "value"]) == 0
    assert duckdb.connect(str(db), read_only=True).execute("select count(*) from ops.payments_totals").fetchone()[0] == 5


@pytest.mark.parametrize("rows, message", [
    ([(1, None, "card", 1.0)], "1 row(s) have no booked_at"),
    ([(1, "2026-11-01", "*", 1.0)], "1 row(s) have pay channel '*'"),
])
def test_totals_refuses_rows_it_cannot_place(tmp_path, rows, message):
    db = tmp_path / "w.duckdb"
    _raw_db(db, rows)
    with pytest.raises(SystemExit) as e:
        main(["totals", "--db", str(db), "--table", "raw.payments", "--out", str(tmp_path / "t.csv"),
              "--date", "booked_at", "--segment", "pay channel", "--amount", "value"])
    assert message in str(e.value)
    assert not (tmp_path / "t.csv").exists()


def test_totals_names_a_missing_column(tmp_path):
    db = tmp_path / "w.duckdb"
    _raw_db(db, [(1, "2026-11-01", "card", 1.0)])
    with pytest.raises(SystemExit) as e:
        main(["totals", "--db", str(db), "--table", "raw.payments", "--out", str(tmp_path / "t.csv")])
    assert "no column 'txn_date' (--date), 'channel' (--segment), 'amount' (--amount)" in str(e.value)


def test_totals_checks_the_output_type_before_reading(tmp_path):
    with pytest.raises(SystemExit) as e:
        main(["totals", "--facts-file", str(tmp_path / "missing.parquet"), "--out", str(tmp_path / "t.xlsx")])
    assert "unsupported output type" in str(e.value)


@pytest.mark.parametrize("argv, message", [
    (["--facts-file", "a.csv", "--db", "w.duckdb", "--table", "t", "--out", "t.csv"], "give --db with --table, or --facts-file"),
    (["--out", "t.csv"], "give --db with --table, or --facts-file"),
    (["--db", "w.duckdb", "--out", "t.csv"], "--db needs --table"),
    (["--facts-file", "a.csv", "--table", "t", "--out", "t.csv"], "--table needs --db"),
    (["--facts-file", "a.csv"], "give --out PATH or --out-table NAME"),
    (["--db", "w.duckdb", "--table", "t", "--out", "t.csv", "--out-table", "x"], "give --out PATH or --out-table NAME"),
    (["--facts-file", "a.csv", "--out-table", "x"], "--out-table writes to the --db database"),
])
def test_totals_argument_errors(argv, message, capsys):
    with pytest.raises(SystemExit) as e:
        main(["totals", *argv])
    assert e.value.code == 2
    assert message in capsys.readouterr().err


def test_totals_will_not_replace_a_table_that_is_not_control_totals(tmp_path):
    db = tmp_path / "w.duckdb"
    _raw_db(db, [(1, "2026-11-01", "card", 1.0)])
    con = duckdb.connect(str(db))
    con.execute("create table raw.ledger as select * from raw.payments")
    con.close()
    with pytest.raises(SystemExit) as e:
        main(["totals", "--db", str(db), "--table", "raw.payments", "--out-table", "raw.ledger",
              "--date", "booked_at", "--segment", "pay channel", "--amount", "value"])
    assert "raw.ledger already exists and isn't a control-totals table" in str(e.value)
    assert duckdb.connect(str(db), read_only=True).execute("select count(*) from raw.ledger").fetchone()[0] == 1


def test_totals_will_not_overwrite_its_source_file(tmp_path, capsys):
    raw = tmp_path / "raw.csv"
    raw.write_text("txn_date,channel,amount\n2026-11-01,card,1\n")
    with pytest.raises(SystemExit):
        main(["totals", "--facts-file", str(raw), "--out", str(raw)])
    assert "--out is the --facts-file" in capsys.readouterr().err
    assert raw.read_text().startswith("txn_date,channel,amount")


@pytest.mark.parametrize("out_table", ["raw.payments", "RAW.Payments", "w.raw.payments"])
def test_totals_will_not_replace_its_source_table_even_with_matching_columns(tmp_path, out_table):
    db = tmp_path / "w.duckdb"
    con = duckdb.connect(str(db))
    con.execute("create schema raw")
    con.execute("create table raw.payments (txn_date date, channel varchar, n bigint, amount double)")
    con.execute("insert into raw.payments values ('2026-11-01', 'card', 7, 1.0), ('2026-11-01', 'wire', 9, 2.0)")
    con.close()
    with pytest.raises(SystemExit) as e:
        main(["totals", "--db", str(db), "--table", "raw.payments", "--out-table", out_table])
    assert "is the --table the totals are read from" in str(e.value)
    con = duckdb.connect(str(db), read_only=True)
    assert con.execute("select * from raw.payments order by channel").fetchall() == [
        (date(2026, 11, 1), "card", 7, 1.0), (date(2026, 11, 1), "wire", 9, 2.0)]
    con.close()


def test_totals_rerun_replaces_an_existing_output_file(tmp_path):
    raw = tmp_path / "raw.csv"
    out = tmp_path / "totals.csv"
    raw.write_text("txn_date,channel,amount\n2026-11-01,card,1\n2026-11-02,card,2\n")
    out.write_text("stale\n")
    assert main(["totals", "--facts-file", str(raw), "--out", str(out)]) == 0
    assert out.read_text().splitlines() == ["txn_date,channel,n,amount", "2026-11-01,*,1,1.0", "2026-11-01,card,1,1.0",
                                            "2026-11-02,*,1,2.0", "2026-11-02,card,1,2.0"]
