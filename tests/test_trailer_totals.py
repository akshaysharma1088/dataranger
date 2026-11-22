"""Control totals read from a delivery file's own trailer record (#86)."""
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from dataranger.cli import main
from dataranger.totals import TrailerFormat, compare_trailer_to_computed, control_totals_from_trailer, read_trailer


def test_read_trailer_delimited_last_line(tmp_path):
    f = tmp_path / "payments_20261101.csv"
    f.write_text("1,P001,10.00\n2,P002,25.50\nTRL,2,35.50\n")
    fmt = TrailerFormat(count_field=1, amount_field=2, record_type_field=0, record_type_value="TRL")
    assert read_trailer(str(f), fmt) == (2, 35.50)


def test_read_trailer_without_a_record_type_marker_uses_the_last_line(tmp_path):
    f = tmp_path / "payments.csv"
    f.write_text("1,P001,10.00\n2,P002,25.50\n2,35.50\n")
    fmt = TrailerFormat(count_field=0, amount_field=1)
    assert read_trailer(str(f), fmt) == (2, 35.50)


def test_read_trailer_fixed_width(tmp_path):
    # positions: record type (0:3), count (3:9), amount (9:20)
    f = tmp_path / "payments.fixed"
    f.write_text(
        "DET0000010.00     \n"
        "DET0000025.50     \n"
        "TRL000002000035.50\n"
    )
    fmt = TrailerFormat(count_field=(3, 9), amount_field=(9, 20), record_type_field=(0, 3), record_type_value="TRL")
    assert read_trailer(str(f), fmt) == (2, 35.50)


def test_read_trailer_sidecar_control_file(tmp_path):
    ctl = tmp_path / "payments_20261101.ctl"
    ctl.write_text("2,35.50\n")
    fmt = TrailerFormat(count_field=0, amount_field=1)
    assert read_trailer(str(ctl), fmt) == (2, 35.50)


def test_control_totals_from_trailer_builds_one_row(tmp_path):
    f = tmp_path / "payments.ctl"
    f.write_text("2,35.50\n")
    fmt = TrailerFormat(count_field=0, amount_field=1)
    totals = control_totals_from_trailer(str(f), fmt, "2026-11-01")
    assert totals.to_dict("records") == [{"txn_date": date(2026, 11, 1), "channel": "*", "n": 2, "amount": 35.50}]
    per_channel = control_totals_from_trailer(str(f), fmt, "2026-11-01", segment="card")
    assert per_channel.iloc[0]["channel"] == "card"


def test_read_trailer_errors_are_readable(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("\n\n")
    with pytest.raises(ValueError, match="has no lines"):
        read_trailer(str(empty), TrailerFormat(count_field=0, amount_field=1))

    f = tmp_path / "payments.csv"
    f.write_text("1,P001,10.00\n")
    with pytest.raises(ValueError, match="no line with field"):
        read_trailer(str(f), TrailerFormat(count_field=0, amount_field=1, record_type_field=0, record_type_value="TRL"))

    bad = tmp_path / "bad.csv"
    bad.write_text("not-a-number,35.50\n")
    with pytest.raises(ValueError, match="could not read the record count"):
        read_trailer(str(bad), TrailerFormat(count_field=0, amount_field=1))


def test_compare_trailer_to_computed_flags_disagreement():
    trailer = pd.DataFrame([{"txn_date": date(2026, 11, 1), "channel": "*", "n": 2, "amount": 35.50}])
    agree = pd.DataFrame([{"txn_date": date(2026, 11, 1), "channel": "*", "n": 2, "amount": 35.50}])
    assert compare_trailer_to_computed(trailer, agree).empty

    disagree = pd.DataFrame([{"txn_date": date(2026, 11, 1), "channel": "*", "n": 3, "amount": 35.50}])
    out = compare_trailer_to_computed(trailer, disagree)
    assert len(out) == 1
    assert out.iloc[0]["n_trailer"] == 2 and out.iloc[0]["n_computed"] == 3

    missing = pd.DataFrame([{"txn_date": date(2026, 11, 2), "channel": "*", "n": 1, "amount": 1.0}])
    assert len(compare_trailer_to_computed(trailer, missing)) == 2  # neither side's row has a match


def test_cli_totals_from_trailer_delimited(tmp_path):
    f = tmp_path / "payments_20261101.csv"
    f.write_text("1,P001,10.00\n2,P002,25.50\nTRL,2,35.50\n")
    out = tmp_path / "totals.csv"
    rc = main(["totals", "--from-trailer", str(f), "--trailer-date", "2026-11-01",
              "--trailer-count-field", "1", "--trailer-amount-field", "2",
              "--trailer-record-type-field", "0", "--trailer-record-type-value", "TRL",
              "--out", str(out)])
    assert rc == 0
    built = pd.read_csv(out)
    assert built.to_dict("records") == [{"txn_date": "2026-11-01", "channel": "*", "n": 2, "amount": 35.5}]


def test_cli_totals_from_trailer_fixed_width(tmp_path):
    f = tmp_path / "payments.fixed"
    f.write_text("TRL000002000035.50\n")
    out = tmp_path / "totals.csv"
    rc = main(["totals", "--from-trailer", str(f), "--trailer-date", "2026-11-01",
              "--trailer-count-field", "3:9", "--trailer-amount-field", "9:20",
              "--trailer-record-type-field", "0:3", "--trailer-record-type-value", "TRL",
              "--out", str(out)])
    assert rc == 0
    built = pd.read_csv(out)
    assert built.iloc[0]["n"] == 2 and built.iloc[0]["amount"] == 35.5


def test_cli_totals_from_trailer_needs_date(tmp_path, capsys):
    f = tmp_path / "payments.ctl"
    f.write_text("2,35.50\n")
    with pytest.raises(SystemExit) as e:
        main(["totals", "--from-trailer", str(f), "--out", str(tmp_path / "t.csv")])
    assert e.value.code == 2
    assert "--from-trailer needs --trailer-date" in capsys.readouterr().err


def test_cli_totals_from_trailer_is_its_own_source(tmp_path, capsys):
    f = tmp_path / "payments.ctl"
    f.write_text("2,35.50\n")
    with pytest.raises(SystemExit) as e:
        main(["totals", "--from-trailer", str(f), "--trailer-date", "2026-11-01",
              "--facts-file", str(f), "--out", str(tmp_path / "t.csv")])
    assert e.value.code == 2
    assert "its own source" in capsys.readouterr().err


def test_cli_totals_from_trailer_reports_disagreement_with_computed_totals(tmp_path):
    trailer_file = tmp_path / "payments.ctl"
    trailer_file.write_text("3,35.50\n")  # trailer says 3 rows; the raw table only has 2
    computed = tmp_path / "computed.csv"
    computed.write_text("txn_date,channel,n,amount\n2026-11-01,*,2,35.50\n")
    with pytest.raises(SystemExit) as e:
        main(["totals", "--from-trailer", str(trailer_file), "--trailer-date", "2026-11-01",
              "--compare-to", str(computed), "--out", str(tmp_path / "t.csv")])
    assert "disagrees with" in str(e.value)
    assert not (tmp_path / "t.csv").exists()


def test_cli_totals_from_trailer_matches_computed_totals(tmp_path):
    trailer_file = tmp_path / "payments.ctl"
    trailer_file.write_text("2,35.50\n")
    computed = tmp_path / "computed.csv"
    computed.write_text("txn_date,channel,n,amount\n2026-11-01,*,2,35.50\n")
    out = tmp_path / "t.csv"
    assert main(["totals", "--from-trailer", str(trailer_file), "--trailer-date", "2026-11-01",
                "--compare-to", str(computed), "--out", str(out)]) == 0
    assert out.exists()


def test_examples_trailers_delimited_fixture(tmp_path):
    f = Path(__file__).resolve().parent.parent / "examples" / "trailers" / "payments_20261101.csv"
    out = tmp_path / "totals.csv"
    rc = main(["totals", "--from-trailer", str(f), "--trailer-date", "2026-11-01",
              "--trailer-record-type-field", "0", "--trailer-record-type-value", "TRL",
              "--trailer-count-field", "1", "--trailer-amount-field", "2", "--out", str(out)])
    assert rc == 0
    built = pd.read_csv(out)
    assert built.to_dict("records") == [{"txn_date": "2026-11-01", "channel": "*", "n": 2, "amount": 35.5}]


def test_examples_trailers_fixed_width_fixture(tmp_path):
    f = Path(__file__).resolve().parent.parent / "examples" / "trailers" / "payments_fixed_20261101.txt"
    out = tmp_path / "totals.csv"
    rc = main(["totals", "--from-trailer", str(f), "--trailer-date", "2026-11-01",
              "--trailer-record-type-field", "0:3", "--trailer-record-type-value", "TRL",
              "--trailer-count-field", "3:9", "--trailer-amount-field", "9:20", "--out", str(out)])
    assert rc == 0
    built = pd.read_csv(out)
    assert built.iloc[0]["n"] == 2 and built.iloc[0]["amount"] == 35.5
