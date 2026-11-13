"""A short Markdown summary of a patrol, for CI job summaries, pull request comments and chat."""
from __future__ import annotations


def markdown(res, table: str, report: str | None = None, limit: int = 20) -> str:
    counts = {d: sum(r["decision"] == d for r in res.incidents) for d in ("auto-fixed", "no action needed", "escalate")}
    lines = [
        f"## DataRanger patrol: `{table}`",
        "",
        f"**{len(res.incidents)} incidents** across {len(res.days)} days and {res.rows:,} rows: "
        f"{counts['auto-fixed']} fix proven safe, {counts['no action needed']} checked and left alone, "
        f"**{counts['escalate']} need a person**.",
    ]
    escalated = [r for r in res.incidents if r["decision"] == "escalate"]
    if escalated:
        lines += ["", "### Needs a person", "", "| Partition | Problems | Best-supported fix |", "|---|---|---|"]
        for r in escalated[:limit]:
            if r.get("table_level"):  # schema changes always go to a person
                lines.append("| whole table | a column was renamed or changed type upstream | none: a person applies the fix |")
                continue
            chosen = r.get("chosen") or {}
            problems = ", ".join(r.get("detectors") or []) or "unknown"
            lines.append(f"| {r.get('partition')} | {problems} | {chosen.get('fix') or 'none proven'} |")
        if len(escalated) > limit:
            lines.append(f"| ... | {len(escalated) - limit} more in the report | |")
    else:
        lines += ["", "Nothing needs a person."]
    lines += ["", "No data was modified. Proposed fixes are in `fixed_version.csv` for review."]
    if report:
        lines.append(f"Full report: `{report}`")
    return "\n".join(lines) + "\n"
