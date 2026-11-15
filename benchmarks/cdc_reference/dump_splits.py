"""Write the tables and the exact training rows run_cells2.py gives conformal cleaning, for the reference implementation."""
import json, sys
import numpy as np
sys.path.insert(0, "src")
from dataranger.bench import realdata
out = {}
for name in realdata.DATASETS:
    case = realdata.load(name)
    case.clean.to_csv(f"benchmarks/cdc_reference/work/{name}_clean.csv", index=False)
    case.dirty.to_csv(f"benchmarks/cdc_reference/work/{name}_dirty.csv", index=False)
    rng = np.random.default_rng(1)
    for s in range(5):
        perm = rng.permutation(len(case.dirty)).tolist()
        cal = perm[: len(perm) // 2]
        out[f"{name}|{s}"] = {"half": cal, "third": cal[: len(cal) // 3], "n": len(perm)}
json.dump(out, open("benchmarks/cdc_reference/work/splits.json", "w"))
print({k: (len(v["third"]), len(v["half"]), v["n"]) for k, v in out.items() if k.endswith("|0")})
