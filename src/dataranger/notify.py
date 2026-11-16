"""Post a patrol summary to a Slack or Microsoft Teams incoming webhook."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit

TIMEOUT = 10  # seconds; a slow chat service must not hold up a nightly job


def post(url: str, text: str, timeout: float = TIMEOUT) -> str | None:
    """POST ``{"text": text}`` as JSON, which Slack and Teams incoming webhooks both accept.

    Returns None on success, else a short reason. The reason never includes the URL: a webhook URL is a secret.
    """
    if urlsplit(url).scheme not in ("http", "https"):
        return "the webhook URL must start with https:// (or http://)"
    request = urllib.request.Request(
        url, data=json.dumps({"text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - scheme checked above
            response.read()
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return f"could not connect ({type(e.reason).__name__})"
    except (OSError, ValueError) as e:  # timeouts, malformed URLs
        return type(e).__name__
    return None
