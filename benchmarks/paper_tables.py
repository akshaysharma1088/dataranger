"""Write the tables of the paper from results/cluster_summary.json, cluster_extra.json and cells2_summary.json.

Usage: python benchmarks/paper_tables.py [path/to/body_exp.tex]
Without a path the rows are printed. With one, the text between `% AUTO:<name> BEGIN` and `% AUTO:<name> END`
is replaced, so no number in those tables is typed by hand.
"""
import json
import re
import sys

c = json.load(open("results/cluster_summary.json"))
t1, t2 = c["table1"], c["table2"]


def pct(x, d=1):
    if x < 5e-6:
        return "0"
    return f"{100 * x:.{d if x < 0.1 else 1}f}\\%"


def row1(label, v):
    return (f"{label} & {pct(v['automated'])} & {pct(v['repair_right'])} & {pct(v['repair_wrong'], 2)} & {pct(v['benign_right'])} & {pct(v['benign_wrong'], 2)}"
            f" & {pct(v['harm'], 2)} & {pct(v['table_harm'], 2)} & {pct(v['errors_fixed'])} \\\\")


def table1():
    out = [row1("Rules engine", t1["rules"]), row1("Monitor-checked", t1["monitors"]), row1("Exact match", t1["reconcile_exact"]),
           row1("Tolerance 1\\%", t1["reconcile_0.01"]), row1("Tolerance 2\\%", t1["reconcile_0.02"]), "\\midrule",
           "\\multicolumn{9}{l}{\\emph{Gate certified per table (Thm.~\\ref{thm:cluster}), residual, 400 tables}} \\\\"]
    out += [row1(f"\\quad $\\alpha={int(a * 100)}\\%$", t1[f"gate_cluster|{a}"]) for a in (0.01, 0.02, 0.05)]
    out += ["\\multicolumn{9}{l}{\\emph{Gate certified per table, learned evidence score, 800 tables}} \\\\"]
    out += [row1(f"\\quad $\\alpha={int(a * 100)}\\%$", t2[f"own|learned_src|{a}"]) for a in (0.01, 0.02, 0.05)]
    out += ["\\midrule", "\\multicolumn{9}{l}{\\emph{Gate certified per incident (Thm.~\\ref{thm:main}; assumes independence)}} \\\\"]
    out += [row1(f"\\quad $\\alpha={int(a * 100)}\\%$", t1[f"gate_incident|{a}"]) for a in (0.01, 0.02, 0.05)]
    return out


def cells2(v1, v5):
    if v1["certified"] == 0 and v5["certified"] == 0:
        return "\\multicolumn{4}{c}{nothing certified}"
    f = lambda v: "\\multicolumn{2}{c}{none}" if v["certified"] == 0 else f"{pct(v['automated'])} & {pct(v['table_harm'], 2)}"
    return f"{f(v1)} & {f(v5)}"


def row2(first, label, key, src=None):
    src = src or t2
    return f"{first} & {label} & {cells2(src[key + '|0.01'], src[key + '|0.05'])} \\\\"


def table2():
    return [row2("Its own", "Invariants", "own|invariants"), row2("", "Team's monitors", "own|monitors"),
            row2("", "Learned, no source", "own|learned_nosrc"), row2("", "Learned, with source", "own|learned_src"),
            row2("", "Residual, partition", "own|residual_partition"), row2("", "Residual, per channel", "own|residual"),
            row2("", "Residual, 800 tables", "gate_cluster_800", t1), "\\midrule",
            row2("Min. residual", "Learned, no source", "min_residual|learned_nosrc"), row2("", "Learned, with source", "min_residual|learned_src"),
            row2("", "Residual", "min_residual|residual"), "\\midrule",
            row2("Default", "Learned, no source", "default|learned_nosrc"), row2("", "Learned, with source", "default|learned_src"),
            row2("", "Residual", "default|residual")]


def table5():
    """Cell repair (Section 6.7) from results/cells2_summary.json. A star marks a gate certified in only some splits."""
    cells = json.load(open("results/cells2_summary.json"))
    p0 = lambda x: f"{100 * x:.0f}"
    end = " \\\\"

    def row(label, d, gated=False):
        if gated and d["certified"] == 0:
            return f" & {label} & \\multicolumn{{3}}{{c}}{{nothing certified}}" + end
        star = "$^*$" if gated and d["certified"] < 1 else ""
        return f" & {label}{star} & {pct(d['errors_fixed'])} & {pct(d['precision'])} & {pct(d['rows_harmed'], 2)}" + end

    out = []
    for name, title in (("hospital", "Hospital"), ("flights", "Flights"), ("beers", "Beers")):
        r = cells[name]
        a, b = r["cdc"], r["cdc_third"]
        block = [row("Baran, 20 tuples", r["baran|budget=20"]), row("Baran, 200 tuples", r["baran|budget=200"]),
                 row("\\quad + gate, 5\\%", r["baran+gate|budget=200|0.05"], True),
                 f" & Conformal, half / third & {p0(a['errors_fixed'])} / {p0(b['errors_fixed'])}\\% & {p0(a['precision'])} / {p0(b['precision'])}\\%"
                 f" & {p0(a['rows_harmed'])} / {p0(b['rows_harmed'])}\\%" + end,
                 row("\\quad + gate, its confidence, 5\\%", r["cdc+gate(confidence)|0.05"], True),
                 row("\\quad + gate, table evidence, 5\\%", r["cdc+gate(evidence)|0.05"], True),
                 row("Own candidates + gate, 5\\%", r["dataranger|budget=half|0.05"], True),
                 row("Own candidates + gate, 1\\%", r["dataranger|budget=half|0.01"], True)]
        block[0] = title + block[0]
        out += block + (["\\midrule"] if name != "beers" else [])
    return out


def table6():
    """The harder mix at the cluster level (Section 6.8) from results/cluster_extra.json. Harm is per table."""
    x = json.load(open("results/cluster_extra.json"))
    end = " \\\\"
    ch, fine = x["hard|channel"], x["hard|channel+category"]

    def row(label, alpha, v, gated=False):
        if gated and v["certified"] == 0:
            return None
        return f"{label} & {alpha} & {pct(v['automated'])} & {pct(v['table_harm'], 2)} & {pct(v['errors_fixed'])}" + end

    def gate_rows(label, src, key, alphas=(0.01, 0.02, 0.05)):
        none = [a for a in alphas if src[f"{key}|{a}"]["certified"] == 0]
        rows, first = [], label
        if none:
            rows.append(f"{first} & {', '.join(str(int(a * 100)) for a in none)}\\% & \\multicolumn{{3}}{{c}}{{nothing certified}}" + end)
            first = ""
        for a in alphas:
            if a not in none:
                rows.append(row(first, f"{int(a * 100)}\\%", src[f"{key}|{a}"]))
                first = ""
        return rows

    num = lambda n: f"{n:,}".replace(",", "{,}")
    out = [f"\\multicolumn{{5}}{{l}}{{\\emph{{Per-channel totals ({num(ch['incidents'])} incidents)}}}}" + end,
           row("Exact match", "--", ch["exact"]), row("Tolerance 1\\%", "--", ch["tolerance_0.01"])]
    out += [row("Shift from original mix" if a == 0.01 else "", f"{int(a * 100)}\\%", x[f"shift|{a}"]) for a in (0.01, 0.05)]
    out += gate_rows("In domain", ch, "gate_cluster") + gate_rows("\\quad split budget", ch, "gate_cluster_grouped")
    out += ["\\midrule", f"\\multicolumn{{5}}{{l}}{{\\emph{{Channel and category totals ({num(fine['incidents'])} incidents)}}}}" + end,
            row("Exact match", "--", fine["exact"]), row("Tolerance 1\\%", "--", fine["tolerance_0.01"])]
    out += gate_rows("In domain", fine, "gate_cluster", (0.01, 0.05))
    return out


blocks = {"table1": table1(), "table2": table2(), "table5": table5(), "table6": table6()}
if len(sys.argv) > 1:
    s = open(sys.argv[1]).read()
    for name, rows in blocks.items():
        pat = re.compile(rf"(% AUTO:{name} BEGIN\n).*?(% AUTO:{name} END)", re.S)
        assert pat.search(s), name
        s = pat.sub(lambda m: m.group(1) + "\n".join(rows) + "\n" + m.group(2), s)
    open(sys.argv[1], "w").write(s)
else:
    for name, rows in blocks.items():
        print(f"%% {name}\n" + "\n".join(rows) + "\n")
