"""Run the tutorial cells with the installed source package and inspect the outputs."""
import base64
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import warnings

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "examples" / "try_dataranger.ipynb"
IFRAME_ADVISORY = "Consider using IPython.display.IFrame instead"


class Tags(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.tags = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def test_notebook_demo_audit_and_dashboard(tmp_path, monkeypatch, capsys):
    pytest.importorskip("IPython", reason="Install the test extra to run the notebook cells")
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    monkeypatch.chdir(tmp_path)
    namespace = {}
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code" and "install" not in cell["metadata"].get("tags", []):
                if cell["id"] == "dashboard":
                    saved_before = {name: (namespace["report_dir"] / name).read_bytes()
                                    for name in ("dashboard.html", "audit.jsonl")}
                # CI already installs the checkout. Do not replace it with the PyPI package.
                exec(compile("".join(cell["source"]), str(NOTEBOOK), "exec"), namespace)
    assert IFRAME_ADVISORY not in [str(item.message) for item in seen]

    records = namespace["records"]
    summary = capsys.readouterr().out
    assert f"{len(records)} incidents:" in summary
    assert any(r["decision"] == "auto-fixed" for r in records)
    assert any(r["decision"] == "escalate" for r in records)
    preview = namespace["audit_preview"]
    assert len(preview) == 5
    assert preview["Decision"].tolist() == [r["decision"] for r in records[:5]]
    assert preview["Chosen candidate"].tolist() == [(r.get("chosen") or {}).get("fix", "None") for r in records[:5]]

    frames = [attrs for tag, attrs in Tags(namespace["dashboard_view"].data).tags if tag == "iframe"]
    assert len(frames) == 1
    frame = frames[0]
    assert frame["title"]
    assert set(frame["sandbox"].split()) == {"allow-scripts", "allow-downloads"}
    page = frame["srcdoc"]
    data = json.loads(page.split('<script type="application/json" id="data">')[1].split("</script>")[0])
    assert len(data["incidents"]) == len(records)
    tags = Tags(page).tags
    assert ("base", {"href": "about:srcdoc"}) in tags
    download = next(attrs for tag, attrs in tags if tag == "a" and attrs.get("download") == "audit.jsonl")
    assert base64.b64decode(download["href"].split(",", 1)[1]) == namespace["audit_path"].read_bytes()
    assert "Your decision is recorded in the audit log" not in page
    assert "It will be used to recalibrate the gate." not in page
    assert "Choices in this demo affect this view only; the saved audit stays unchanged." in page
    assert "This preview does not save decisions." in page
    assert {name: (namespace["report_dir"] / name).read_bytes() for name in saved_before} == saved_before
    original = (namespace["report_dir"] / "dashboard.html").read_text(encoding="utf-8")
    assert 'href="audit.jsonl"' in original
    assert "about:srcdoc" not in original


def test_demo_error_prints_diagnostic_before_raising(tmp_path, monkeypatch, capsys):
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    cell = next(cell for cell in notebook["cells"] if cell["id"] == "demo")
    real_run = subprocess.run

    def run_with_bad_option(args, **kwargs):
        return real_run([*args, "--unknown-demo-option"], **kwargs)

    monkeypatch.setattr(subprocess, "run", run_with_bad_option)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(subprocess.CalledProcessError) as error:
        exec(compile("".join(cell["source"]), str(NOTEBOOK), "exec"), {})
    diagnostic = capsys.readouterr().out
    assert "unrecognized arguments: --unknown-demo-option" in diagnostic
    assert error.value.returncode == 2
    assert error.value.stdout == diagnostic


def test_dashboard_advisory_filter_is_scoped(tmp_path, monkeypatch):
    display = pytest.importorskip("IPython.display", reason="Install the test extra to run the notebook cells")
    html_class = display.HTML

    def html_with_unrelated_warning(*args, **kwargs):
        warnings.warn_explicit("Unrelated display warning", UserWarning,
                               filename="IPython/core/display.py", lineno=1, module="IPython.core.display")
        return html_class(*args, **kwargs)

    monkeypatch.setattr(display, "HTML", html_with_unrelated_warning)
    (tmp_path / "dashboard.html").write_text(
        '<html><head></head><body><a href="audit.jsonl">Audit log</a></body></html>', encoding="utf-8")
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text('{}\n', encoding="utf-8")
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    cell = next(cell for cell in notebook["cells"] if cell["id"] == "dashboard")
    namespace = {"report_dir": tmp_path, "audit_path": audit_path}
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        filters_before = list(warnings.filters)
        exec(compile("".join(cell["source"]), str(NOTEBOOK), "exec"), namespace)
        assert warnings.filters == filters_before
        # The original advisory must still appear outside the notebook's narrow scope.
        html_class('<iframe src="about:blank"></iframe>')
    assert [str(item.message) for item in seen] == ["Unrelated display warning", IFRAME_ADVISORY]


def test_colab_badge_targets_the_notebook():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    url = "https://colab.research.google.com/github/akshaysharma1088/dataranger/blob/main/"
    assert f"]({url}{NOTEBOOK.relative_to(ROOT).as_posix()})" in readme
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    for index, cell in enumerate(notebook["cells"]):
        if cell["cell_type"] == "code":
            assert index and notebook["cells"][index - 1]["cell_type"] == "markdown"
