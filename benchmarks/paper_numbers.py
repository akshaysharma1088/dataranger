"""Print the Q7 table rows and budget sentences for numbers.tex from results/cells2_summary.json."""
import json

c = json.load(open("results/cells2_summary.json"))
pct = lambda x: f"{100 * x:.1f}\\%"
w = lambda x: f"{x:.2f}"


def row(label, d):
    return f" & {label} & {pct(d['errors_fixed'])} & {pct(d['precision'])} & {w(d['wrong_per_1000_cells'])} \\\\"


rows = []
for name, title in (("hospital", "Hospital"), ("flights", "Flights"), ("beers", "Beers")):
    r = c[name]
    block = [
        row("Baran, 20 tuples", r["baran|budget=20"]),
        row("Baran, 200 tuples", r["baran|budget=200"]),
        row("Baran 200 + gate, $\\alpha=5\\%$", r["baran+gate|budget=200|0.05"]),
        row("Conformal cleaning", r["cdc"]),
        row("Conformal + gate on its confidence, 5\\%", r["cdc+gate(confidence)|0.05"]),
        row("Conformal + gate on table evidence, 5\\%", r["cdc+gate(evidence)|0.05"]),
        row("DataRanger, 200 rows, $\\alpha=5\\%$", r["dataranger|budget=200|0.05"]),
        row("DataRanger, half, $\\alpha=5\\%$", r["dataranger|budget=half|0.05"]),
        row("DataRanger, half, $\\alpha=1\\%$", r["dataranger|budget=half|0.01"]),
    ]
    block[0] = title + block[0]
    rows += block + (["\\midrule"] if name != "beers" else [])
print("\n".join(rows))
print()
for name in ("hospital", "flights", "beers"):
    r = c[name]
    print(name, "budget curve alpha=5%:", {b: (round(100 * r[f'dataranger|budget={b}|0.05']['errors_fixed'], 1), round(r[f'dataranger|budget={b}|0.05']['wrong_per_1000_cells'], 2), r[f'dataranger|budget={b}|0.05'].get('certified'))
                                            for b in (20, 50, 100, 200, 500, 'half')})
    print(name, "baran budgets:", {b: (round(100 * r[f'baran|budget={b}']['errors_fixed'], 1), round(r[f'baran|budget={b}']['wrong_per_1000_cells'], 2)) for b in (20, 50, 100, 200)})
    print(name, "baran+gate 1%/5% by budget:", {b: (round(100 * r[f'baran+gate|budget={b}|0.05']['errors_fixed'], 1), round(r[f'baran+gate|budget={b}|0.05']['wrong_per_1000_cells'], 2), round(r[f'baran+gate|budget={b}|0.01']['wrong_per_1000_cells'], 2)) for b in (20, 50, 100, 200)})
    print(name, "cdc gates:", {k: (round(100 * r[k]['errors_fixed'], 1), round(r[k]['wrong_per_1000_cells'], 2), r[k].get('certified')) for k in r if k.startswith("cdc")})
    print(name, "dataranger 1% by budget:", {b: (round(100 * r[f'dataranger|budget={b}|0.01']['errors_fixed'], 1), round(r[f'dataranger|budget={b}|0.01']['wrong_per_1000_cells'], 2), r[f'dataranger|budget={b}|0.01'].get('certified')) for b in (20, 50, 100, 200, 500, 'half')})
