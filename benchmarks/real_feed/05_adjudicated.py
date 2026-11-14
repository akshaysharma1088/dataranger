"""What the gate did on the state-days a person adjudicated (paper, Section 6.9).
Run after 03_gate.py, from the working directory. Argument: path to results/real_feed/adjudication_labels.csv."""
import json, sys
import pandas as pd
from scipy.stats import beta
labels = pd.read_csv(sys.argv[1])
inc = pd.read_csv('work/incidents_0.01.csv.gz')
summary = json.load(open('work/gate_summary_0.01.json'))
m = labels.merge(inc[['date', 'state', 'fix', 'score', 'fix_wrong']], left_on=['Date', 'State'], right_on=['date', 'state'], how='left')
out = {'labels': labels.Label.value_counts().to_dict()}
r = labels[labels.Sample == 'random']; k, n = int((r.Label == 'error').sum()), len(r)
out['random_sample'] = {'errors': k, 'n': n, 'ci95': [float(beta.ppf(.025, k, n - k + 1)), float(beta.ppf(.975, k + 1, n - k))]}
for alpha in ('0.02', '0.05'):
    lam = summary['test'][f'gate_incident|{alpha}']['lambda']
    def action(row):
        if pd.isna(row.score): return 'no alert'
        if row.score > lam: return 'escalated'
        return ('reloaded' if row.fix == 'reload' else 'kept') + (' wrong' if row.fix_wrong else ' right')
    out[f'actions|{alpha}'] = {lab: g.apply(action, axis=1).value_counts().to_dict() for lab, g in m.groupby('Label')}
json.dump(out, open('work/adjudicated_summary.json', 'w'), indent=1)
print(json.dumps(out, indent=1))
