import json

import duckdb
import pandas as pd

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
    assert (tmp_path / "dashboard.html").read_text().count("<tr>") >= len(res.incidents)
    for line in open(tmp_path / "audit.jsonl"):
        assert '"correct"' not in line and '"kinds"' not in line


def test_cli_demo_and_patrol(tmp_path, capsys):
    assert main(["demo", "--out", str(tmp_path / "demo")]) == 0
    case = inject(generate(6, "small", n_days=40))
    db = str(tmp_path / "w.duckdb")
    con = duckdb.connect(db)
    con.register("t", case.warehouse["transactions"]); con.execute("create table facts as select * from t")
    con.register("c", case.warehouse["customers"]); con.execute("create table customers as select * from c")
    con.register("m", case.manifest); con.execute("create table manifest as select * from m")
    con.close()
    assert main(["patrol", "--db", db, "--table", "facts", "--ref-table", "customers", "--manifest-table", "manifest",
                 "--category", "category", "--out", str(tmp_path / "rep")]) == 0
    audit = [json.loads(l) for l in open(tmp_path / "rep" / "audit.jsonl")]
    labels = pd.DataFrame({"partition": [a["partition"] for a in audit if not a["table_level"]], "verdict": "right"})
    labels.to_csv(tmp_path / "labels.csv", index=False)
    assert main(["calibrate", "--audit", str(tmp_path / "rep" / "audit.jsonl"), "--labels", str(tmp_path / "labels.csv")]) == 0
    assert "not enough" in capsys.readouterr().out  # a handful of incidents cannot certify 1%
