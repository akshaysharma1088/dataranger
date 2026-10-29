"""LLM proposer (stages 2 and 3): diagnose one incident and choose a fix template with parameters.

The model never touches data. It returns JSON naming one template from a fixed menu; the code
applies it to a copy, and the gate decides. Any OpenAI-compatible or Ollama endpoint works; the
benchmark uses open-weights Qwen2.5 through Ollama, so it costs nothing to rerun.
"""
from __future__ import annotations

import json
import re
import urllib.request

from . import fixes as F

MENU = """Choose exactly one action:
- none: the data is correct; the alert reflects a real event or a stale control total
- dedupe: remove rows that repeat the same txn_id; set "keep" to "first" (earliest loaded copy) or "last" (latest loaded copy)
- dedupe_business: remove rows that repeat the same customer, amount, category and channel under new txn_ids
- reload: replace the partition with a fresh copy from the source (only if reload_from_source_available is true)
- rescale: multiply amounts by "factor", optionally only for one "channel"
- quarantine: move rows that reference unknown customers out of the table
- escalate: you are not sure; hand the incident to a person"""

PROMPT = """You are a data engineer on call. One daily partition of a transactions table raised alerts.
The source system publishes control totals (source_rows, source_amount); they can be slightly stale.

Incident:
{context}

{menu}

Reply with JSON only: {{"diagnosis": "<one sentence>", "action": "<action>", "channel": "<channel or null>", "factor": <number or null>, "keep": "<first, last or null>"}}"""


def prompt(context: dict) -> str:
    return PROMPT.format(context=json.dumps(context, indent=1), menu=MENU)


def parse(text: str) -> tuple[F.Fix | None, str]:
    """Turn a model reply into a fix template. None means escalate (or an unusable reply)."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None, ""
    try:
        j = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None, ""
    a = str(j.get("action", "")).strip().lower()
    diag = str(j.get("diagnosis", ""))[:300]
    if a == "rescale":
        try:
            factor = float(j.get("factor"))
        except (TypeError, ValueError):
            return None, diag
        ch = j.get("channel")
        return (F.make("rescale", factor=factor, channel=ch) if ch not in (None, "", "null", "all")
                else F.make("rescale", factor=factor)), diag
    if a == "dedupe":
        keep = str(j.get("keep") or "first").lower()
        return (F.make("dedupe", key="txn_id", keep="last") if keep == "last" else F.make("dedupe", key="txn_id")), diag
    if a in ("none", "dedupe_business", "reload", "quarantine"):
        return F.make(a), diag
    return None, diag


def call(text: str, model: str = "qwen2.5:7b", url: str = "http://localhost:11434/api/generate",
         timeout: int = 600) -> str:
    body = json.dumps({"model": model, "prompt": text, "stream": False, "format": "json",
                       "options": {"temperature": 0, "num_predict": 200, "seed": 0}}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["response"]
