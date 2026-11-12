import json

import duckdb
import pandas as pd
import pytest

from dataranger.bench import generate, inject
from dataranger.cli import main
from dataranger.patrol import Mapping, patrol, write_outputs


def test_patrol_with_renamed_columns_never_touches_input(tmp_path):
    case = inject(generate(5, "small", n_days=40))
    facts = case.warehouse["transactions"].rename(columns={"txn_id": "id", "amount": "amt", "channel": "src"})
    before = facts.copy()
    man = case.manifest.rename(columns={"channel": "src"})
    res = patrol(facts, case.warehouse["customers"], man.rename(columns={"txn_date": "txn_date"}),
                 Mapping(key="id", amount="amt", segment="src"))
    pd.testing.assert_frame_equal(facts, before)
    assert {"id", "amt", "src"} <= set(res.fixed_table.columns)
    write_outputs(res, tmp_path)
    page = (tmp_path / "dashboard.html").read_text()
    data = json.loads(page.split('<script type="application/json" id="data">')[1].split("</script>")[0])
    assert len(data["incidents"]) == len(res.incidents)
    assert '"correct"' not in page and '"kinds"' not in page
    for line in open(tmp_path / "audit.jsonl"):
        assert '"correct"' not in line and '"kinds"' not in line


def test_cli_demo_and_patrol(tmp_path, capsys):
    assert main(["demo", "--out", str(tmp_path / "demo")]) == 0
    assert "fixed" in capsys.readouterr().out
    case = inject(generate(6, "small", n_days=40))
    db = str(tmp_path / "w.duckdb")
    con = duckdb.connect(db)
    con.register("t", case.warehouse["transactions"]); con.execute("create table facts as select * from t")
    con.register("c", case.warehouse["customers"]); con.execute("create table customers as select * from c")
    con.register("m", case.manifest); con.execute("create table manifest as select * from m")
    con.close()
    assert main(["patrol", "--db", db, "--table", "facts", "--ref-table", "customers", "--manifest-table", "manifest",
                 "--category", "category", "--out", str(tmp_path / "rep")]) == 0
    assert "fixed" in capsys.readouterr().out
    audit = [json.loads(l) for l in open(tmp_path / "rep" / "audit.jsonl")]
    labels = pd.DataFrame({"partition": [a["partition"] for a in audit if not a["table_level"]], "verdict": "right"})
    labels.to_csv(tmp_path / "labels.csv", index=False)
    assert main(["calibrate", "--audit", str(tmp_path / "rep" / "audit.jsonl"), "--labels", str(tmp_path / "labels.csv")]) == 0
    assert "not enough" in capsys.readouterr().out  # a handful of incidents cannot certify 1%


def test_cli_patrol_reads_csv_files(tmp_path, capsys):
    case = inject(generate(7, "small", n_days=40))
    case.warehouse["transactions"].to_csv(tmp_path / "facts.csv", index=False)
    case.warehouse["customers"].to_csv(tmp_path / "customers.csv", index=False)
    case.manifest.to_csv(tmp_path / "manifest.csv", index=False)
    out = tmp_path / "rep"
    assert main(["patrol", "--facts-file", str(tmp_path / "facts.csv"), "--ref-file", str(tmp_path / "customers.csv"),
                 "--manifest-file", str(tmp_path / "manifest.csv"), "--category", "category",
                 "--out", str(out)]) == 0
    assert (out / "dashboard.html").exists()
    # --table is optional on the file path: the report title falls back to the facts file name.
    assert "DataRanger patrol: facts" in (out / "dashboard.html").read_text()
    assert "incidents" in capsys.readouterr().out


def test_cli_patrol_rejects_mixed_or_partial_sources(tmp_path):
    csv = str(tmp_path / "facts.csv")
    pd.DataFrame({"a": [1]}).to_csv(csv, index=False)
    mixed = ["patrol", "--facts-file", csv, "--ref-file", csv, "--manifest-file", csv,
             "--db", "w.duckdb", "--table", "facts", "--ref-table", "customers", "--manifest-table", "manifest"]
    with pytest.raises(SystemExit):
        main(mixed)
    with pytest.raises(SystemExit):
        main(["patrol"])  # neither source given
    with pytest.raises(SystemExit):
        main(["patrol", "--facts-file", csv])  # file source but incomplete


def test_patrol_rejects_missing_key_column():
    case = inject(generate(5, "small", n_days=40))
    facts = case.warehouse["transactions"]
    man = case.manifest
    with pytest.raises(SystemExit, match=r"facts table has no column 'wrong_id' \(--key\)"):
        patrol(facts, case.warehouse["customers"], man, Mapping(key="wrong_id"))


def test_patrol_rejects_missing_ref_key():
    case = inject(generate(5, "small", n_days=40))
    facts = case.warehouse["transactions"]
    man = case.manifest
    with pytest.raises(SystemExit, match=r"reference table has no column 'missing_cust' \(--ref-key\)"):
        patrol(facts, case.warehouse["customers"], man, Mapping(ref_key="missing_cust"))


def test_cli_patrol_wrong_key_exits_clearly(tmp_path, capsys):
    case = inject(generate(8, "small", n_days=40))
    db = str(tmp_path / "w.duckdb")
    con = duckdb.connect(db)
    con.register("t", case.warehouse["transactions"])
    con.execute("create table transactions as select * from t")
    con.register("c", case.warehouse["customers"])
    con.execute("create table customers as select * from c")
    con.register("m", case.manifest)
    con.execute("create table control_totals as select * from m")
    con.close()
    with pytest.raises(SystemExit) as ei:
        main(["patrol", "--db", db, "--table", "transactions", "--ref-table", "customers",
              "--manifest-table", "control_totals", "--key", "wrong_id", "--out", str(tmp_path / "o")])
    # SystemExit from our raise carries the message; argparse uses code too
    msg = str(ei.value)
    if msg in ("0", "1", "2"):
        msg = capsys.readouterr().err or capsys.readouterr().out
    assert "wrong_id" in msg and "--key" in msg
