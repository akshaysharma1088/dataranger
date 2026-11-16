"""Out-of-range gate numbers and an empty labels file stop with a short argparse error (exit 2), not a traceback."""
import pytest

from dataranger.cli import gate_threshold, main, probability


def fails_with_usage_error(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err
    return err


@pytest.mark.parametrize("value", ["5", "0.51", "-0.1", "nan", "abc"])
def test_patrol_rejects_threshold_outside_the_grid(value, capsys):
    err = fails_with_usage_error(["patrol", "--threshold", value], capsys)
    assert "threshold must be" in err and "from 0 to 0.5" in err


@pytest.mark.parametrize("flag", ["--alpha", "--delta"])
@pytest.mark.parametrize("value", ["2", "1", "0", "-0.5", "nan", "x"])
def test_calibrate_rejects_non_probabilities(flag, value, tmp_path, capsys):
    labels = tmp_path / "labels.csv"
    labels.write_text("partition,verdict\n2026-01-01,right\n")
    argv = ["calibrate", "--audit", str(tmp_path / "audit.jsonl"), "--labels", str(labels), flag, value]
    err = fails_with_usage_error(argv, capsys)
    assert f"{flag[2:]} must be" in err and "strictly between 0 and 1" in err


@pytest.mark.parametrize("content", ["", "partition,verdict\n"])
def test_calibrate_rejects_empty_labels_file(content, tmp_path, capsys):
    labels = tmp_path / "labels.csv"
    labels.write_text(content)
    err = fails_with_usage_error(["calibrate", "--audit", str(tmp_path / "audit.jsonl"), "--labels", str(labels)], capsys)
    assert f"{labels} has no reviewed incidents" in err


def test_valid_values_are_unchanged():
    assert gate_threshold("0") == 0.0
    assert gate_threshold("0.0135") == 0.0135
    assert gate_threshold("0.5") == 0.5
    assert probability["alpha"]("0.01") == 0.01
    assert probability["delta"]("0.999") == 0.999
