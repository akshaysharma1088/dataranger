import pandas as pd
import pytest

from dataranger.bench import detect, generate, inject, score
from dataranger.bench import realdata
from dataranger.bench.detect_cells import detect_cells, prf
from dataranger.bench.inject import EXPECTED
from dataranger.cli import main


@pytest.fixture(scope="module")
def case():
    inst = generate(seed=3, size="small", n_days=40)
    return inst, inject(inst)


def test_generation_is_deterministic():
    a, b = generate(5, "small", n_days=20), generate(5, "small", n_days=20)
    pd.testing.assert_frame_equal(a.source_txn, b.source_txn)


def test_every_issue_has_an_expected_decision(case):
    _, c = case
    assert {i.kind for i in c.issues} == set(EXPECTED)
    for i in c.issues:
        assert (i.fix["action"], i.decision) == EXPECTED[i.kind]


def test_decoys_change_the_source_and_errors_do_not(case):
    inst, c = case
    assert len(c.source["transactions"]) > len(inst.source_txn)  # volume-spike decoys add real rows
    src_ids = set(c.source["transactions"]["txn_id"])
    for i in c.issues:
        if i.kind == "late_partition":
            d = pd.Timestamp(i.partition).date()
            assert (c.source["transactions"]["txn_date"] == d).any()
            assert not (c.warehouse["transactions"]["txn_date"] == d).any()
        if i.kind == "broken_refs":
            assert set(i.fix["rows"]) <= src_ids


def test_detectors_catch_structural_errors(case):
    inst, c = case
    s = score(detect(c.warehouse, inst.days), c.issues)
    by_kind = {r["kind"]: r for r in s["issues"]}
    for k in ("duplicates", "late_partition", "broken_refs", "schema_drift", "unit_scale"):
        assert by_kind[k]["caught"], k


def test_cell_detectors_on_a_tiny_table(tmp_path):
    d = tmp_path / "toy"
    d.mkdir()
    clean = pd.DataFrame({"zip": ["60601"] * 12 + ["10001"] * 12, "city": ["chicago"] * 12 + ["new york"] * 12,
                          "oz": ["12"] * 24})
    dirty = clean.copy()
    dirty.loc[1, "city"] = "chicagx"
    dirty.loc[2, "oz"] = "12.0 oz"
    dirty.loc[3, "oz"] = "12 OZ."
    clean.to_csv(d / "clean.csv", index=False)
    dirty.to_csv(d / "dirty.csv", index=False)
    rc = realdata.load("toy", path=d)
    assert rc.errors == {(1, "city"), (2, "oz"), (3, "oz")}
    found = detect_cells(rc.dirty)["all"]
    assert prf(found, rc.errors)["recall"] == 1.0


def test_cli_bench(capsys):
    assert main(["bench", "--size", "small", "--seed", "2"]) == 0
    assert "false alarms" in capsys.readouterr().out
