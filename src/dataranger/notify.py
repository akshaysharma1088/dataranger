"""Post a patrol summary to a Slack or Microsoft Teams incoming webhook."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit

TIMEOUT = 10  # seconds; a slow chat service must not hold up a nightly job

# Teams webhooks made with the Workflows app (the replacement for the retired Office 365
# connectors) are served from these hosts and want an Adaptive Card, not a bare "text".
TEAMS_WORKFLOW_HOSTS = (".logic.azure.com", ".powerplatform.com")


def payload(url: str, text: str) -> dict:
    """The JSON body for ``url``: ``{"text": text}`` for Slack and classic Teams webhooks,
    an Adaptive Card message holding the same text for a Teams Workflows webhook."""
    host = (urlsplit(url).hostname or "").lower()
    if not host.endswith(TEAMS_WORKFLOW_HOSTS):
        return {"text": text}
    card = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard",
            "version": "1.4", "body": [{"type": "TextBlock", "text": text, "wrap": True}]}
    return {"type": "message",
            "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]}


def post(url: str, text: str, timeout: float = TIMEOUT) -> str | None:
    """POST the summary as JSON (see ``payload``).

    Returns None on success, else a short reason. The reason never includes the URL: a webhook URL is a secret.
    """
    try:
        if urlsplit(url).scheme not in ("http", "https"):
            return "the webhook URL must start with https:// (or http://)"
        request = urllib.request.Request(
            url, data=json.dumps(payload(url, text)).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - scheme checked above
            response.read()
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return f"could not connect ({type(e.reason).__name__})"
    except (OSError, ValueError) as e:  # timeouts, malformed URLs such as "http://["
        return type(e).__name__
    return None
