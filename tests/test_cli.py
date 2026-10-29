from dataranger import __version__
from dataranger.cli import main


def test_version_string():
    assert __version__.count(".") == 2


def test_plan_lists_six_stages(capsys):
    assert main(["plan"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 6 and out[3].startswith("4. Verify")
