import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from dataranger.bench import generate, inject
from dataranger.cli import main
from dataranger.notify import payload, post


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


@pytest.mark.parametrize("url", ["http://[", "https://[::1/hooks/x"])
def test_malformed_url_is_a_short_failure_not_a_traceback(url):
    reason = post(url, "hi")
    assert reason == "ValueError"
    assert "hooks" not in reason


@pytest.mark.parametrize("url", ["https://hooks.slack.com/services/T/B/x",
                                 "https://example.webhook.office.com/webhookb2/x"])
def test_slack_and_classic_teams_get_bare_text(url):
    assert payload(url, "3 need a person") == {"text": "3 need a person"}


@pytest.mark.parametrize("url", [
    "https://prod-12.westus.logic.azure.com:443/workflows/abc/triggers/manual/paths/invoke?sig=x",
    "https://default1234.56.environment.api.powerplatform.com/powerautomate/automations/direct/x",
])
def test_teams_workflows_get_an_adaptive_card_with_the_same_text(url):
    body = payload(url, "| table |\n3 need a person")
    assert body["type"] == "message"
    (attachment,) = body["attachments"]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    card = attachment["content"]
    assert card["type"] == "AdaptiveCard"
    assert card["body"] == [{"type": "TextBlock", "text": "| table |\n3 need a person", "wrap": True}]

@pytest.mark.parametrize("url", ["https://discord.com/api/webhooks/1/abc", "https://discordapp.com/api/webhooks/1/abc",
                                 "https://ptb.discord.com/api/webhooks/1/abc"])
def test_discord_gets_content_not_text(url):
    assert payload(url, "3 need a person") == {"content": "3 need a person"}


def test_discord_summary_over_the_limit_is_cut_at_a_line_break_with_a_count():
    rows = [f"| 2026-01-{day:02d} | nulls | dedupe |" for day in range(1, 31)] * 3
    text = "\n".join(["## DataRanger patrol", "", "| Partition | Problems | Best-supported fix |", "|---|---|---|"] + rows)
    assert len(text) > 2000
    content = payload("https://discord.com/api/webhooks/1/abc", text)["content"]
    assert set(payload("https://discord.com/api/webhooks/1/abc", text)) == {"content"}
    assert len(content) <= 2000
    body, last = content.rsplit("\n", 1)
    assert all(line in text.splitlines() for line in body.splitlines())
    kept = sum(line.startswith("| 2026") for line in body.splitlines())
    assert last == f"... {len(rows) - kept} more partitions left out, see the report."


def test_slack_payload_is_not_shortened():
    text = "x\n" * 3000
    assert payload("https://hooks.slack.com/services/T/B/x", text) == {"text": text}
