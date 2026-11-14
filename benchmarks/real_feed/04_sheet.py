"""Adjudication sample: the 100 largest and 50 random state-days whose first-published value differed from the value the
publisher had settled on 30 days later by more than 1%. A person labels each as an entry/load error, a restatement, or unclear."""
import datetime, re
import numpy as np, pandas as pd
df = pd.read_csv('work/state_days.csv.gz')
def rel(a, b): return (a - b).abs() / b.clip(lower=1)
rc, rd = rel(df.cases_first, df.cases_d30), rel(df.deaths_first, df.deaths_d30)
df['measure'] = np.where(rc >= rd, 'cases', 'deaths'); df['size'] = np.maximum(rc, rd)
w = df[df['size'] > 0.01].copy()
pick = lambda m, k: np.where(w.measure == 'cases', w[f'cases_{k}'], w[f'deaths_{k}'])
for k in ('first', 'd7', 'd30', 'final', 'nyt', 'prev'): w[k] = pick(None, k)
g1, g30 = rel(w['first'], w['nyt']), rel(w['d30'], w['nyt'])
w['suggested'] = np.select([(g30 < g1) & (g30 <= 0.01), (g1 <= 0.01) & (g30 > g1)], ['error', 'restatement'], 'unclear')
w.loc[w['nyt'].isna(), 'suggested'] = 'unclear'
big = w.sort_values('size', ascending=False).head(100)
rest = w.drop(big.index).sample(50, random_state=20261113)
s = pd.concat([big.assign(sample='largest'), rest.assign(sample='random')]).sort_values(['date', 'state']).reset_index(drop=True)
# the publisher's own modification log, matched by state and nearest date
log = []
MONTHS = {m: i for i, m in enumerate(['January','February','March','April','May','June','July','August','September','October','November','December'], 1)}
for line in open('work/jhu_readme.md').read().splitlines()[90:740]:
    m = re.match(r'\*\s*([A-Z][a-z]+)\s+(\d{1,2}),?\s+(\d{4})\s*\|(.*)', line)
    if m and m.group(1) in MONTHS:
        log.append((datetime.date(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2))), m.group(4).strip()))
def match(state, date):
    d = datetime.date.fromisoformat(date)
    hits = sorted((abs((ld - d).days), ld, t) for ld, t in log if state.lower() in t.lower() and -3 <= (ld - d).days <= 45)
    return ' || '.join(f'{ld}: {t[:230]}' for _, ld, t in hits[:2])
s['publisher_log'] = [match(a, b) for a, b in zip(s.state, s.date)]
s['issues_search'] = ['https://github.com/CSSEGISandData/COVID-19/issues?q=' + st.replace(' ', '+') + '+' + datetime.date.fromisoformat(d).strftime('%m-%d-%Y') for st, d in zip(s.state, s.date)]
out = pd.DataFrame({'id': range(1, len(s) + 1), 'date': s.date, 'state': s.state, 'measure': s.measure, 'first_published': s['first'].astype(int),
                    'source_7_days_later': s['d7'].astype(int), 'settled_30_days_later': s['d30'].astype(int), 'final': s['final'].astype('Int64'),
                    'nyt_same_day': s['nyt'].astype('Int64'), 'previous_day_first_published': s['prev'].astype('Int64'),
                    'first_vs_settled_pct': (100 * (s['first'] / s['d30'].clip(lower=1) - 1)).round(1), 'sample': s['sample'], 'suggested_label': s.suggested,
                    'publisher_log_entries_nearby': s.publisher_log, 'issue_search_link': s.issues_search, 'your_label': '', 'your_note': ''})
out.to_csv('work/adjudication_sample.csv', index=False)
print(len(w), 'materially wrong state-days;', len(log), 'log entries parsed; sample', len(out), '| suggested:', out.suggested_label.value_counts().to_dict(),
      '| with a matching log entry:', int((out.publisher_log_entries_nearby != '').sum()))
print(out[['date', 'state', 'measure', 'first_published', 'settled_30_days_later', 'nyt_same_day', 'first_vs_settled_pct', 'suggested_label']].head(6).to_string(index=False))
