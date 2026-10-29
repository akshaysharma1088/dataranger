"""Figures for the DataRanger paper, drawn from the JSON summaries in results/. Usage: python benchmarks/figures.py results figures"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

R = Path(sys.argv[1])  # results folder
OUT = Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
plt.rcParams.update({"font.family": "serif", "font.size": 8, "axes.spines.top": False, "axes.spines.right": False,
                     "pdf.fonttype": 42})
INK, BLUE, ORANGE, GREEN, RED, GREY = "#1d1d1b", "#2d5be3", "#d9822b", "#1f7a4d", "#b42318", "#8a8a85"

# 1. Architecture
fig, ax = plt.subplots(figsize=(7.0, 1.45))
ax.set_xlim(0, 10); ax.set_ylim(0, 4.4); ax.axis("off")
stages = [("Detect", "SQL + stats\nreconciliation"), ("Propose", "templates\nor LLM"), ("Verify", "control totals\n+ invariants"),
          ("Gate", "residual ≤ λ\n(LTT)"), ("Report", "console\n+ audit log")]
xs = [0.1, 2.1, 4.1, 6.1, 8.1]
for (name, sub), x in zip(stages, xs):
    fc = "#e8eefc" if name == "Gate" else "#f3f2ee"
    ax.add_patch(FancyBboxPatch((x, 2.0), 1.75, 1.6, boxstyle="round,pad=0.02,rounding_size=0.15", fc=fc, ec=INK, lw=0.6))
    ax.text(x + 0.875, 3.15, name, ha="center", va="center", fontsize=8.5, weight="bold")
    ax.text(x + 0.875, 2.5, sub, ha="center", va="center", fontsize=7, color="#444")
for x in xs[:-1]:
    ax.annotate("", xy=(x + 2.0, 2.8), xytext=(x + 1.78, 2.8), arrowprops=dict(arrowstyle="-|>", lw=0.6, color=INK))
ax.annotate("", xy=(6.2, 0.75), xytext=(6.98, 1.98), arrowprops=dict(arrowstyle="-|>", lw=0.6, color=ORANGE))
ax.text(4.0, 0.45, "escalate to a person", fontsize=7.5, color=ORANGE, ha="center")
ax.annotate("", xy=(7.9, 0.75), xytext=(7.0, 1.98), arrowprops=dict(arrowstyle="-|>", lw=0.6, color=GREEN))
ax.text(8.8, 0.45, "apply as new version", fontsize=7.5, color=GREEN, ha="center")
ax.text(1.0, 4.15, "Source of record: control totals per day and channel", fontsize=7, color="#555")
ax.annotate("", xy=(4.98, 3.62), xytext=(4.98, 4.0), arrowprops=dict(arrowstyle="-|>", lw=0.5, color=GREY))
ax.annotate("", xy=(0.98, 3.62), xytext=(0.98, 4.0), arrowprops=dict(arrowstyle="-|>", lw=0.5, color=GREY))
fig.savefig(OUT / "fig_arch.pdf", bbox_inches="tight"); plt.close(fig)

# 2. Harm versus automation
s = json.load(open(R / "repair_summary.json"))
fig, ax = plt.subplots(figsize=(3.4, 2.2))
for ev, col, lab in (("residual", BLUE, "fixed threshold, per-channel totals"),
                     ("residual_partition", ORANGE, "fixed threshold, partition totals only")):
    pts = sorted((v["auto_rate"], v["harm_mean"]) for k, v in s["fixed_threshold"].items() if k.startswith(ev + "|"))
    ax.plot([p[0] * 100 for p in pts], [p[1] * 100 for p in pts], "o--", ms=2.5, lw=0.8, color=col, label=lab)
for ev, col, m in (("residual", BLUE, "*"), ("residual_partition", ORANGE, "*")):
    pts = [(v["auto_rate_mean"], v["harm_mean"], v["alpha"]) for k, v in s["calibrated"].items() if k.startswith(ev + "|")]
    ax.scatter([p[0] * 100 for p in pts], [p[1] * 100 for p in pts], marker=m, s=40, color=col, edgecolor=INK, lw=0.4, zorder=5)
    if ev == "residual":
        for a, h, al in pts:
            ax.annotate(f"calibrated, α={al:.0%}", (a * 100, h * 100), xytext=(-12, 7), textcoords="offset points", fontsize=5.5, color=INK)
for name, lab, col in (("rules", "rules", RED), ("monitors", "monitor-checked", RED)):
    v = s["all"][name]
    ax.scatter([v["auto_rate"] * 100], [v["harm"] * 100], marker="X", s=30, color=col, zorder=5)
    ax.annotate(lab, (v["auto_rate"] * 100, v["harm"] * 100), xytext=(4, -2), textcoords="offset points", fontsize=6, color=col)
ax.set_yscale("log"); ax.set_ylim(0.1, 40)
ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
ax.set_xlabel("incidents handled without a person (%)"); ax.set_ylabel("silent harm (%)")
ax.legend(fontsize=5.5, frameon=False, loc="lower right")
fig.savefig(OUT / "fig_tradeoff.pdf", bbox_inches="tight"); plt.close(fig)

# 3. Drift
st = json.load(open(R / "stream_summary.json"))["0.02"]
months = list(range(4, 13))
fig, axs = plt.subplots(1, 2, figsize=(3.4, 1.6))
for p, col, lab in (("static", RED, "static"), ("epoch", ORANGE, "epoch recalibration"), ("epoch_memory", BLUE, "epochs + memory")):
    axs[0].plot(months, [st[p][str(m)]["harm"] * 100 for m in months], "o-", ms=2, lw=0.9, color=col, label=lab)
    axs[1].plot(months, [st[p][str(m)]["auto"] * 100 for m in months], "o-", ms=2, lw=0.9, color=col)
axs[0].axhline(2, color=GREY, lw=0.6, ls=":"); axs[0].text(12, 2.2, "α", fontsize=6, color=GREY, ha="right")
for a in axs:
    a.axvline(5, color=GREY, lw=0.5, ls="--"); a.axvline(9, color=GREY, lw=0.5, ls="--"); a.set_xlabel("month")
axs[0].set_ylabel("silent harm (%)"); axs[1].set_ylabel("automated (%)")
fig.legend(*axs[0].get_legend_handles_labels(), fontsize=5.5, frameon=False, loc="upper center", ncol=3, bbox_to_anchor=(0.5, 1.08))
fig.tight_layout(); fig.savefig(OUT / "fig_drift.pdf", bbox_inches="tight"); plt.close(fig)
print("figures written")

# 4. Real datasets
cs = json.load(open(R / "cells_summary.json"))
ds = ["hospital", "flights", "beers"]
meth = [("baran_20", "Baran (20 tuples)", "#8a8a85"), ("cdc_0.999", "Conformal cleaning", ORANGE),
        ("dataranger_0.01", "DataRanger, α=1%", "#9bb3f0"), ("dataranger_0.05", "DataRanger, α=5%", BLUE)]
fig, axs = plt.subplots(1, 2, figsize=(3.4, 1.7))
w = 0.2
for k, (m, lab, col) in enumerate(meth):
    xs = [i + (k - 1.5) * w for i in range(3)]
    axs[0].bar(xs, [cs[d][m]["errors_fixed"] * 100 for d in ds], w, color=col, label=lab)
    axs[1].bar(xs, [max(cs[d][m]["wrong_per_1000_cells"], 0.01) for d in ds], w, color=col)
axs[1].set_ylim(0.01, 200)
for a in axs:
    a.set_xticks(range(3)); a.set_xticklabels(["Hosp.", "Flights", "Beers"], fontsize=6.5)
axs[0].set_ylabel("errors fixed (%)"); axs[1].set_ylabel("wrong changes / 1k cells")
axs[1].set_yscale("log"); axs[1].yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
fig.legend(*axs[0].get_legend_handles_labels(), fontsize=5.3, frameon=False, loc="upper center", ncol=2, bbox_to_anchor=(0.5, 1.12))
fig.tight_layout(); fig.savefig(OUT / "fig_real.pdf", bbox_inches="tight"); plt.close(fig)

# 5. Calibration-set size
ab = json.load(open(R / "ablations.json"))
fig, ax = plt.subplots(figsize=(3.4, 1.6))
for a, col in (("0.01", BLUE), ("0.05", ORANGE)):
    d = ab["size"][a]
    ns = sorted(d, key=int)
    ax.plot([d[n]["incidents"] for n in ns], [d[n]["auto"] * 100 for n in ns], "o-", ms=2.5, lw=0.9, color=col,
            label=f"α={float(a):.0%}")
ax.set_xscale("log"); ax.set_xlabel("labeled calibration incidents"); ax.set_ylabel("automated (%)")
ax.set_xticks([150, 300, 1000, 3000]); ax.minorticks_off()
ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
ax.legend(fontsize=6, frameon=False, loc="lower right")
fig.savefig(OUT / "fig_size.pdf", bbox_inches="tight"); plt.close(fig)
print("extra figures written")
