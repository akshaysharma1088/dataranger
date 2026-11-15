"""Table 7 columns added for review: whether keep-all and reload-all certify on 2020-2021, and test harm split by cause.
Avoidable: a wrong value kept although the source held the right one at patrol time. Run from the working directory."""
import json
import pandas as pd
from dataranger.repair.gate import hb_pvalue
inc = pd.read_csv('work/incidents_0.01.csv.gz'); S = json.load(open('work/gate_summary_0.01.json'))
cal, test = inc[inc.date < '2022-01-01'], inc[inc.date >= '2022-01-01']
def split(ap, fix):
    keep, rel = ap & (fix != 'reload'), ap & (fix == 'reload')
    return {'automated': float(ap.mean()), 'harm_wrong_change': float((rel & test.reload_wrong & ~test.none_wrong).mean()),
            'harm_revised_after_patrol': float(((keep & test.none_wrong & test.reload_wrong) | (rel & test.reload_wrong & test.none_wrong)).mean()),
            'harm_avoidable_kept': float((keep & test.none_wrong & ~test.reload_wrong).mean())}
all_ = pd.Series(True, index=test.index)
out = {'keep_every_load': split(all_, pd.Series('none', index=test.index)), 'reload_every_alert': split(all_, pd.Series('reload', index=test.index))}
for a in ('0.02', '0.05'):
    out[f'gate|{a}'] = split(test.score <= S['test'][f'gate_incident|{a}']['lambda'], test.fix)
for name, col in (('keep_every_load', 'none_wrong'), ('reload_every_alert', 'reload_wrong')):
    out[name]['cal_harm'] = float(cal[col].mean())
    out[name]['certified'] = {a: bool(hb_pvalue(cal[col].mean(), len(cal), a) <= 0.05) for a in (0.01, 0.02, 0.05)}
json.dump(out, open('work/harm_decomposition.json', 'w'), indent=1)
print(json.dumps(out, indent=1))
