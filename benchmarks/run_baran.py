"""Run Raha detection + Baran correction (Mahdavi et al.) with a labeling budget; save corrected cells."""
import json, sys, time, random
import numpy as np
import pandas as pd
if not hasattr(pd.DataFrame, "applymap"):
    pd.DataFrame.applymap = pd.DataFrame.map
import raha

name, budget, seed, cache, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5]
random.seed(seed); np.random.seed(seed)
dd = {"name": name, "path": f"{cache}/{name}/dirty.csv", "clean_path": f"{cache}/{name}/clean.csv"}
t = time.time()
det = raha.Detection(); det.LABELING_BUDGET = budget; det.VERBOSE = False
# Record the tuples Raha's detection labels with ground truth, so later steps can keep them out of certification and test rows.
det_labeled = set()
_label = det.label_with_ground_truth
def _recording(d):
    _label(d)
    det_labeled.update(int(x) for x in d.labeled_tuples)
det.label_with_ground_truth = _recording
detected = det.run(dd)
cor = raha.Correction(); cor.LABELING_BUDGET = budget; cor.VERBOSE = False; cor.SAVE_RESULTS = False
d = raha.dataset.Dataset(dd)
d.detected_cells = dict(detected)
cor.run(d)
corr = {f"{r}|{c}": v for (r, c), v in d.corrected_cells.items()}
labeled = sorted(int(x) for x in d.labeled_tuples)
json.dump({"dataset": name, "budget": budget, "seed": seed, "seconds": time.time() - t,
           "detected": len(detected), "corrections": corr, "labeled_tuples": labeled,
           "detection_labeled_tuples": sorted(det_labeled)}, open(out, "w"))
print(name, budget, seed, "detected", len(detected), "corrections", len(corr), f"{time.time()-t:.0f}s")
