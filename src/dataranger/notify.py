"""Post a patrol summary to a Slack, Microsoft Teams or Discord incoming webhook."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

TIMEOUT = 10  # seconds; a slow chat service must not hold up a nightly job

# Teams webhooks made with the Workflows app (the replacement for the retired Office 365
# connectors) are served from these hosts and want an Adaptive Card, not a bare "text".
TEAMS_WORKFLOW_HOSTS = (".logic.azure.com", ".powerplatform.com")

# Discord webhooks want {"content": ...} and refuse a message of more than 2,000 characters.
DISCORD_HOSTS = ("discord.com", "discordapp.com")
DISCORD_LIMIT = 2000


def _discord_content(text: str) -> str:
    """``text`` cut at a line break to fit Discord's limit, ending with a line that counts what was left out."""
    if len(text) <= DISCORD_LIMIT:
        return text
    lines = text.splitlines()
    kept: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) + 1 > DISCORD_LIMIT - 80:  # room for the closing line
            break
        kept.append(line)
        size += len(line) + 1
    left_out = 0
    for line in lines[len(kept):]:
        more = re.search(r"\|\s*\.\.\.\s*\|\s*(\d+) more", line)
        if more:
            left_out += int(more.group(1))
        elif line.startswith("| ") and not line.startswith("| Partition"):
            left_out += 1
    note = f"... {left_out} more partitions left out, see the report." if left_out else "... cut to fit Discord's limit."
    return "\n".join(kept) + "\n" + note


def payload(url: str, text: str) -> dict:
    """The JSON body for ``url``: ``{"text": text}`` for Slack and classic Teams webhooks,
    ``{"content": text}`` for Discord (cut to its 2,000 character limit), an Adaptive Card message
    holding the same text for a Teams Workflows webhook."""
    host = (urlsplit(url).hostname or "").lower()
    if host in DISCORD_HOSTS or host.endswith(tuple("." + h for h in DISCORD_HOSTS)):
        return {"content": _discord_content(text)}
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
