"""One row per state-day: the value as first published (the warehouse copy), the source as of D+7 (what a reload returns
at patrol time), the source as of D+30 (the settled value used as ground truth), the final value, and NYT's totals for the
same state-day as of D+7 (the independent evidence). Also partition-level load defects of the first-published file."""
import datetime, io, json, subprocess
import numpy as np, pandas as pd
V = json.load(open('work/versions.json'))
def blob(repo, oid):
    return subprocess.run(['git', '-C', repo, 'cat-file', 'blob', oid], capture_output=True, text=True).stdout
cache = {}
def jhu(oid):
    if oid not in cache:
        cache[oid] = pd.read_csv(io.StringIO(blob('jhu', oid)))
    return cache[oid]
rows, parts = [], []
nyt_cache = {}
for d, r in sorted(V.items()):
    first = jhu(r['first'])
    ref_states = set(jhu(r['final']).Province_State)
    dup = int(first.Province_State.duplicated().sum()); junk = int((~first.Province_State.isin(ref_states)).sum())
    parts.append({'date': d, 'rows_first': len(first), 'duplicate_keys': dup, 'junk_rows': junk, 'columns': len(first.columns),
                  'first_published': r['first_date'], 'versions': r['n_versions']})
    t = {k: jhu(r[k]).drop_duplicates('Province_State').set_index('Province_State') for k in ('first', 'd7', 'd30', 'final')}
    if r['nyt7'] not in nyt_cache:
        nyt_cache.clear(); nyt_cache[r['nyt7']] = blob('nyt', r['nyt7']) if r['nyt7'] else ''
    nyt = {}
    for line in nyt_cache[r['nyt7']].splitlines():
        if line.startswith(d):
            p = line.split(','); nyt[p[1]] = (float(p[3]), float(p[4]))
    for s in ref_states:
        if s not in t['first'].index or s not in t['d30'].index:
            continue
        row = {'date': d, 'state': s}
        for k in t:
            row[f'cases_{k}'] = t[k].at[s, 'Confirmed'] if s in t[k].index else np.nan
            row[f'deaths_{k}'] = t[k].at[s, 'Deaths'] if s in t[k].index else np.nan
        row['cases_nyt'], row['deaths_nyt'] = nyt.get(s, (np.nan, np.nan))
        rows.append(row)
    cache.clear()
df = pd.DataFrame(rows).sort_values(['state', 'date']).reset_index(drop=True)
for m in ('cases', 'deaths'):
    df[f'{m}_prev'] = df.groupby('state')[f'{m}_first'].shift(1)      # the warehouse's own previous partition
df.to_csv('work/state_days.csv.gz', index=False); pd.DataFrame(parts).to_csv('work/partitions.csv', index=False)
p = pd.DataFrame(parts)
print(len(df), 'state-days;', df.state.nunique(), 'states;', df.cases_nyt.notna().sum(), 'with NYT totals as of D+7')
print('partitions with junk rows:', int((p.junk_rows > 0).sum()), '| duplicate keys:', int((p.duplicate_keys > 0).sum()), '| column counts:', p['columns'].value_counts().to_dict())
