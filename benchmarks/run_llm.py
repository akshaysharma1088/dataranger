"""Ask an open-weights LLM to propose a fix for each incident (run on GitHub Actions with Ollama).

Incidents come from regenerating benchmark cases, which is deterministic, so no data is shipped.
Usage: python benchmarks/run_llm.py --model qwen2.5:7b --shard 0 --nshards 20 --seeds 60
Writes results/llm/<model>/shard_<k>.jsonl.
"""
import argparse
import json
import time
from pathlib import Path

from dataranger.bench import generate, inject
from dataranger.repair import llm
from dataranger.repair.agent import run

ap = argparse.ArgumentParser()
ap.add_argument("--model", default="qwen2.5:7b")
ap.add_argument("--shard", type=int, default=0)
ap.add_argument("--nshards", type=int, default=1)
ap.add_argument("--seeds", type=int, default=60)
ap.add_argument("--size", default="small")
args = ap.parse_args()

out = Path("results/llm") / args.model.replace(":", "_") / f"shard_{args.shard}.jsonl"
out.parent.mkdir(parents=True, exist_ok=True)
done = set()
if out.exists():
    done = {(j["seed"], j["partition"]) for j in map(json.loads, open(out))}
with open(out, "a") as f:
    for seed in range(1, args.seeds + 1):
        if seed % args.nshards != args.shard:
            continue
        for r in run(inject(generate(seed, args.size))):
            if r["table_level"] or (seed, r["partition"]) in done:
                continue
            t = time.time()
            try:
                text = llm.call(llm.prompt(r["context"]), args.model)
            except Exception as e:  # keep going; a failed call counts as escalate
                text = f"ERROR {e}"
            fx, diag = llm.parse(text)
            f.write(json.dumps({"size": args.size, "seed": seed, "partition": r["partition"], "kinds": r["kinds"],
                                "response": text, "fix": str(fx) if fx else None, "diagnosis": diag,
                                "seconds": round(time.time() - t, 1)}) + "\n")
            f.flush()
