"""Print every cell-repair setting from results/cells2_summary.json (Section 6.7): the numbers quoted in the text.
The table itself is written by benchmarks/paper_tables.py."""
import json

c = json.load(open("results/cells2_summary.json"))
pct = lambda x, d=1: f"{100 * x:.{d}f}\\%"


def row(label, d, gated=False):
    if gated and d["certified"] == 0:
        return f" & {label} & \\multicolumn{{3}}{{c}}{{nothing certified}} \\\\"
    note = "" if not gated or d["certified"] == 1 else "$^*$"
    return f" & {label}{note} & {pct(d['errors_fixed'])} & {pct(d['precision'])} & {pct(d['rows_harmed'], 2)} \\\\"


rows = []
for name, title in (("hospital", "Hospital"), ("flights", "Flights"), ("beers", "Beers")):
    r = c[name]
    block = [row("Baran, 20 tuples", r["baran|budget=20"]), row("Baran, 200 tuples", r["baran|budget=200"]),
             row("\\quad + gate, 5\\%", r["baran+gate|budget=200|0.05"], True),
             row("\\quad + gate, 1\\%", r["baran+gate|budget=200|0.01"], True),
             row("Conformal cleaning (half)", r["cdc"]), row("Conformal cleaning (third)", r["cdc_third"]),
             row("\\quad + gate, own confidence, 5\\%", r["cdc+gate(confidence)|0.05"], True),
             row("\\quad + gate, table evidence, 5\\%", r["cdc+gate(evidence)|0.05"], True),
             row("DataRanger candidates + gate, 5\\%", r["dataranger|budget=half|0.05"], True),
             row("DataRanger candidates + gate, 1\\%", r["dataranger|budget=half|0.01"], True)]
    block[0] = title + block[0]
    rows += block + (["\\midrule"] if name != "beers" else [])
print("\n".join(rows))
print()
for name in ("hospital", "flights", "beers"):
    r = c[name]
    for k, v in r.items():
        if isinstance(v, dict) and "certified" in v:
            print(name, k, f"fixed {v['errors_fixed']:.3f} harmed {v['rows_harmed']:.4f} cert {v['certified']:.2f} exceeds {v['exceeds']:.2f} c={v['cutoff']:.1f} n={v['cert_rows']:.0f}/{v['population_rows']:.0f}")
