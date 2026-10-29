"""Stage 1 results: how well the deterministic detectors find issues, before any repair.

Synthetic: 3 sizes x 5 seeds, issue-level recall per type, decoys flagged, false alarms.
Real: cell-level precision, recall and F1 on Hospital, Flights and Beers.
Writes results/detection.json.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

from dataranger.bench import detect, generate, inject, score
from dataranger.bench import realdata
from dataranger.bench.detect_cells import detect_cells, prf

out = {"synthetic": {}, "real": {}}
for size in ("small", "medium", "large"):
    caught, fa = defaultdict(list), []
    for seed in range(1, 6):
        inst = generate(seed, size)
        case = inject(inst)
        s = score(detect(case.warehouse, inst.days), case.issues)
        for r in s["issues"]:
            caught[r["kind"]].append(r["flagged"] if r["decoy"] else r["caught"])
        fa.append(len(s["false_alarms"]))
    out["synthetic"][size] = {"rate": {k: float(np.mean(v)) for k, v in caught.items()},
                              "n": {k: len(v) for k, v in caught.items()},
                              "false_alarms_per_instance": float(np.mean(fa))}

ENTITY = {"flights": "flight"}
for name in realdata.DATASETS:
    c = realdata.load(name)
    res = {"zero_config": {k: prf(v, c.errors) for k, v in detect_cells(c.dirty).items()}, "summary": c.summary()}
    if name in ENTITY:
        res["with_entity_key"] = prf(detect_cells(c.dirty, ENTITY[name])["all"], c.errors)
    out["real"][name] = res

Path("results").mkdir(exist_ok=True)
json.dump(out, open("results/detection.json", "w"), indent=1)
for size, r in out["synthetic"].items():
    print(size, {k: round(v, 2) for k, v in r["rate"].items()}, "false alarms/instance", r["false_alarms_per_instance"])
for name, r in out["real"].items():
    print(name, r["zero_config"]["all"], r.get("with_entity_key", ""))
