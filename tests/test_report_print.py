"""The dashboard carries print styles and stays one self-contained file."""
from dataranger.cli import main


def test_dashboard_has_print_styles(tmp_path):
    assert main(["demo", "--out", str(tmp_path)]) == 0
    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    block = html[html.index("@media print{"):]
    block = block[:block.index("</style>")]
    assert "break-inside:avoid" in block            # cards are not cut across pages
    assert "--surface:#ffffff" in block             # white page even when the system is in dark mode
    assert ".side,.toast,.actions,.tabs{display:none}" in block
    assert 'attr(href)' in block                    # links are shown as text
    assert "print-color-adjust:exact" in block      # the three marks keep their shapes on paper
    assert "@media (prefers-color-scheme:dark)" in html
