"""The gate on real revisions of the JHU US state feed.
Warehouse copy = value as first published. Patrol one week later: candidates are `none` (keep it) and `reload` (take the
source as it stands at D+7). Evidence = gap to NYT's independently reported totals as of D+7. Ground truth = the source as
of D+30; a value is wrong if it differs from it by more than MATERIAL on cases or deaths.
Calibrate on 2020-2021, test on 2022-2023 (a forward split). Writes work/gate_summary.json."""
import json, sys
import numpy as np, pandas as pd
from dataranger.repair.gate import GRID, hb_pvalue
MATERIAL, DELTA, SPLIT = float(sys.argv[1]) if len(sys.argv) > 1 else 0.01, 0.05, '2022-01-01'
df = pd.read_csv('work/state_days.csv.gz')
def rel(a, b): return (a - b).abs() / b.clip(lower=1)
def wrong(prefix): return (rel(df[f'cases_{prefix}'], df.cases_d30) > MATERIAL) | (rel(df[f'deaths_{prefix}'], df.deaths_d30) > MATERIAL)
def resid(prefix): return pd.concat([rel(df[f'cases_{prefix}'], df.cases_nyt), rel(df[f'deaths_{prefix}'], df.deaths_nyt)], axis=1).max(axis=1, skipna=False).fillna(np.inf)
df['none_wrong'], df['reload_wrong'] = wrong('first'), wrong('d7')
df['r_none'], df['r_reload'] = resid('first'), resid('d7')
changed = (df.cases_first != df.cases_d7) | (df.deaths_first != df.deaths_d7)
drop = (df.cases_first < df.cases_prev) | (df.deaths_first < df.deaths_prev)
jump = ((df.cases_prev >= 1000) & (rel(df.cases_first, df.cases_prev) > 0.02)) | ((df.deaths_prev >= 100) & (rel(df.deaths_first, df.deaths_prev) > 0.03))
recon = df.r_none > 0.001
df['alerts'] = changed.map({True: 'source_revised ', False: ''}) + drop.map({True: 'count_fell ', False: ''}) + jump.map({True: 'jump ', False: ''}) + recon.map({True: 'gap_to_totals', False: ''})
inc = df[df.alerts != ''].copy()
# selection: the candidate closer to the independent totals; ties keep the warehouse copy
use_reload = changed[inc.index] & (inc.r_reload < inc.r_none)
inc['fix'] = np.where(use_reload, 'reload', 'none')
inc['score'] = np.where(use_reload, inc.r_reload, inc.r_none)
inc['fix_wrong'] = np.where(use_reload, inc.reload_wrong, inc.none_wrong)
inc['real'] = inc.none_wrong                                   # the warehouse copy was materially wrong
cal, test = inc[inc.date < SPLIT], inc[inc.date >= SPLIT]
def certify(d, alpha, cluster=None):
    lam = None
    for g in GRID:
        loss = ((d.score <= g) & d.fix_wrong).astype(float)
        if cluster is not None:
            loss = loss.groupby(cluster).mean()
        if hb_pvalue(loss.mean(), len(loss), alpha) <= DELTA: lam = float(g)
        else: break
    return lam
def outcome(d, ap):
    return {'automated': float(ap.mean()), 'harm': float((ap & d.fix_wrong).mean()), 'errors_fixed': float((ap & ~d.fix_wrong & d.real).sum() / max(d.real.sum(), 1)),
            'reloads_applied': float((ap & (d.fix == 'reload')).mean())}
out = {'material': MATERIAL, 'state_days': len(df), 'incidents': len(inc), 'cal_incidents': len(cal), 'test_incidents': len(test),
       'real_errors_cal': int(cal.real.sum()), 'real_errors_test': int(test.real.sum()),
       'alerts': {k: int(inc.alerts.str.contains(k).sum()) for k in ('source_revised', 'count_fell', 'jump', 'gap_to_totals')},
       'errors_never_alerted': int(df.none_wrong.sum() - inc.none_wrong.sum())}
out['test'] = {'keep_everything': {'automated': 1.0, 'harm': float(test.none_wrong.mean()), 'errors_fixed': 0.0},
               'always_reload': {'automated': 1.0, 'harm': float(test.reload_wrong.mean()), 'errors_fixed': float((test.real & ~test.reload_wrong).sum() / max(test.real.sum(), 1))}}
for tol in (0.0, 0.005, 0.01, 0.02):
    out['test'][f'tolerance_{tol}'] = outcome(test, test.score <= tol)
cl_cal = cal.state + cal.date.str[:7]
for alpha in (0.01, 0.02, 0.05):
    for name, lam in (('incident', certify(cal, alpha)), ('state_month', certify(cal, alpha, cl_cal))):
        ap = (test.score <= lam) if lam is not None else pd.Series(False, index=test.index)
        o = outcome(test, ap) | {'lambda': lam}
        if name == 'state_month':
            o['harm_per_cluster'] = float((ap & test.fix_wrong).groupby(test.state + test.date.str[:7]).mean().mean())
        out['test'][f'gate_{name}|{alpha}'] = o
out['clusters_cal'] = int(cl_cal.nunique())
json.dump(out, open(f'work/gate_summary_{MATERIAL}.json', 'w'), indent=1)
inc.to_csv(f'work/incidents_{MATERIAL}.csv.gz', index=False)
print({k: v for k, v in out.items() if k != 'test'})
for k, v in out['test'].items(): print(f'{k:26s}', {a: (round(b, 4) if isinstance(b, float) else b) for a, b in v.items()})
