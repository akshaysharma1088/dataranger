import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from dataranger.bench import generate, inject
from dataranger.cli import main
from dataranger.notify import post


@pytest.fixture
def webhook():
    """A local incoming-webhook stand-in that records each JSON body it gets."""
    received, status = [], {"code": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append({"type": self.headers["Content-Type"], "body": json.loads(body)})
            self.send_response(status["code"])
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}/hooks/secret-token", received=received,
                          status=status)
    server.shutdown()
    server.server_close()


@pytest.fixture
def patrol_args(tmp_path):
    case = inject(generate(7, "small", n_days=40))
    for name, frame in (("facts", case.warehouse["transactions"]),
                        ("refs", case.warehouse["customers"]),
                        ("manifest", case.manifest)):
        frame.to_csv(tmp_path / f"{name}.csv", index=False)
    return ["patrol", "--facts-file", str(tmp_path / "facts.csv"),
            "--ref-file", str(tmp_path / "refs.csv"),
            "--manifest-file", str(tmp_path / "manifest.csv"),
            "--table", "gold.payments", "--out", str(tmp_path / "report")]


@pytest.fixture
def clean_patrol(tmp_path, monkeypatch):
    import dataranger.patrol as patrol_module

    monkeypatch.setattr(patrol_module, "from_files", lambda *args: (None, None, None))
    monkeypatch.setattr(patrol_module, "patrol",
                        lambda *args, **kwargs: SimpleNamespace(incidents=[], days=[1, 2], rows=10))
    monkeypatch.setattr(patrol_module, "write_outputs", lambda *args, **kwargs: None)
    return ["patrol", "--facts-file", "facts.csv", "--ref-file", "refs.csv",
            "--manifest-file", "manifest.csv", "--table", "gold.payments", "--out", str(tmp_path)]


def test_escalation_posts_the_summary(webhook, patrol_args, capsys):
    assert main(patrol_args + ["--notify-webhook", webhook.url]) == 0
    assert len(webhook.received) == 1
    message = webhook.received[0]
    assert message["type"] == "application/json"
    text = message["body"]["text"]
    assert set(message["body"]) == {"text"}
    assert "gold.payments" in text and "need a person" in text and "### Needs a person" in text
    assert "secret-token" not in capsys.readouterr().err


def test_webhook_is_read_from_the_environment(webhook, patrol_args, monkeypatch):
    monkeypatch.setenv("DATARANGER_WEBHOOK", webhook.url)
    assert main(patrol_args + ["--fail-on-escalate"]) == 2
    assert len(webhook.received) == 1


def test_clean_patrol_posts_nothing_unless_always(webhook, clean_patrol):
    assert main(clean_patrol + ["--notify-webhook", webhook.url]) == 0
    assert webhook.received == []
    assert main(clean_patrol + ["--notify-webhook", webhook.url, "--notify-always"]) == 0
    assert len(webhook.received) == 1
    assert "Nothing needs a person." in webhook.received[0]["body"]["text"]


def test_no_webhook_posts_nothing(webhook, clean_patrol, monkeypatch):
    monkeypatch.delenv("DATARANGER_WEBHOOK", raising=False)
    assert main(clean_patrol + ["--notify-always"]) == 0
    assert webhook.received == []


@pytest.mark.parametrize("fail_on_escalate, expected_code", [(False, 0), (True, 2)])
def test_failed_post_is_a_note_and_keeps_the_exit_code(webhook, patrol_args, capsys, fail_on_escalate,
                                                       expected_code):
    webhook.status["code"] = 500
    args = patrol_args + ["--notify-webhook", webhook.url, "--json"]
    if fail_on_escalate:
        args.append("--fail-on-escalate")
    assert main(args) == expected_code
    captured = capsys.readouterr()
    json.loads(captured.out)  # --json output stays clean
    assert "could not post to the webhook: HTTP 500" in captured.err
    assert "secret-token" not in captured.err and "127.0.0.1" not in captured.err


def test_post_never_names_the_url():
    # Port 9 (discard) on localhost refuses the connection in a test environment.
    reason = post("http://127.0.0.1:9/hooks/secret-token", "hi", timeout=2)
    assert reason and "secret-token" not in reason and "127.0.0.1" not in reason
    assert post("ftp://example.com/hook", "hi") == "the webhook URL must start with https:// (or http://)"
