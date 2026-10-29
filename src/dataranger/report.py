"""The morning report: one self-contained HTML page per patrol run."""
from __future__ import annotations

import html
import math

CSS = """
:root{--bg:#f7f6f2;--card:#fff;--ink:#1d1d1b;--muted:#6b6a64;--line:#e4e2da;--green:#1f7a4d;--amber:#a15c00;--red:#b42318;--accent:#2d5be3}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1d1d1b;--ink:#ecebe6;--muted:#a3a29b;--line:#33332f;--green:#4cc38a;--amber:#f0a94b;--red:#f97066;--accent:#7a9cff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1080px;margin:0 auto;padding:28px 16px 60px}h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 10px}
.sub{color:var(--muted);margin:0 0 22px}.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}.tile b{display:block;font-size:28px;font-variant-numeric:tabular-nums}
.tile span{color:var(--muted);font-size:13px}.ok b{color:var(--green)}.warn b{color:var(--amber)}.bad b{color:var(--red)}
.wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}table{border-collapse:collapse;width:100%;min-width:640px}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top;font-size:14px}th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em}
tr:last-child td{border-bottom:0}code{font:13px ui-monospace,Menlo,monospace}.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;border:1px solid var(--line)}
td:first-child,.pill{white-space:nowrap}.p-esc{color:var(--amber)}.p-fix{color:var(--green)}.p-ok{color:var(--muted)}.note{color:var(--muted);font-size:13px;margin-top:10px}
"""


def _gap(c) -> str:
    if not c:
        return "n/a"
    r = c["residual"]
    return "invariant broken" if math.isinf(r) else f"{100 * r:.2f}%"


def _row(r: dict) -> str:
    c = r.get("chosen")
    alerts = ", ".join(r.get("detectors", [])) or "schema change"
    pill = {"auto-fixed": "p-fix", "no action needed": "p-ok"}.get(r["decision"], "p-esc")
    why = ""
    if r["decision"] == "escalate":
        if r["table_level"]:
            why = "Schema changes always go to a person."
        elif not c:
            why = "No candidate fix."
        elif c["tier"] != "auto":
            why = f"Best fix <code>{html.escape(c['fix'])}</code> deletes data or changes meaning."
        elif math.isinf(c["residual"]):
            why = f"Best fix <code>{html.escape(c['fix'])}</code> would still break a table invariant (duplicate keys, unknown references or an empty partition)."
        else:
            why = f"Best fix <code>{html.escape(c['fix'])}</code> leaves a {_gap(c)} gap to the source, above the threshold."
    else:
        why = f"<code>{html.escape(r.get('applied', ''))}</code> matches the source within {_gap(c)}."
    return (f"<tr><td>{html.escape(str(r['partition'] or 'whole table'))}</td><td>{html.escape(alerts)}</td>"
            f"<td><span class='pill {pill}'>{html.escape(r['decision'])}</span></td><td>{why}</td></tr>")


def render(incidents: list[dict], title: str, run_date: str) -> str:
    n = len(incidents)
    fixed = [r for r in incidents if r["decision"] == "auto-fixed"]
    benign = [r for r in incidents if r["decision"] == "no action needed"]
    esc = [r for r in incidents if r["decision"] == "escalate"]
    head = "<tr><th>Partition</th><th>Alerts</th><th>Decision</th><th>Evidence</th></tr>"
    section = lambda rows: f"<div class='wrap'><table>{head}{''.join(_row(r) for r in rows)}</table></div>" if rows \
        else "<p class='note'>Nothing here.</p>"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{CSS}</style></head><body><main>
<h1>{html.escape(title)}</h1><p class="sub">Run {html.escape(run_date)}. What broke, what DataRanger fixed, and what needs you.</p>
<div class="tiles"><div class="tile"><b>{n}</b><span>incidents</span></div>
<div class="tile bad"><b>{len(esc)}</b><span>need a person</span></div>
<div class="tile ok"><b>{len(fixed)}</b><span>fixed automatically</span></div>
<div class="tile warn"><b>{len(benign)}</b><span>checked, no action needed</span></div></div>
<h2>Needs you</h2>{section(esc)}
<h2>Fixed automatically</h2>{section(fixed)}
<h2>Checked, no action needed</h2>{section(benign)}
<p class="note">Every fix was checked against the source's control totals and the table's invariants before it was applied,
and written as a new version. The full evidence for each decision is in audit.jsonl.</p>
</main></body></html>"""
