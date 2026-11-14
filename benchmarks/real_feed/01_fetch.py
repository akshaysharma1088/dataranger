"""Fetch every version needed for the real-incident study (blobless clones in jhu/ and nyt/).
JHU US state daily report for date D: as first published, and as of D+7, D+30, final.
NYT us-states.csv as of D+7 (the independent totals available at patrol time)."""
import collections, datetime, json, re, subprocess
DIR = 'csse_covid_19_data/csse_covid_19_daily_reports_us'
def git(repo, *a, inp=None):
    return subprocess.run(['git', '-C', repo, *a], capture_output=True, text=True, input=inp).stdout
hist = collections.defaultdict(list); cur = None
for l in git('jhu', 'log', '--format=C %H %cI', '--name-status', '--', DIR).splitlines():
    if l.startswith('C '): cur = (l.split()[1], l.split()[2][:10]); continue
    m = re.match(r'[AM]\t(.*\d\d-\d\d-\d{4}\.csv)$', l)
    if m: hist[m.group(1)].append(cur)                       # newest first
final = {l.split('\t')[1]: l.split()[2] for l in git('jhu', 'ls-tree', 'HEAD', DIR + '/').splitlines()}
nyt_hist = [(l.split()[0], l.split()[1][:10]) for l in git('nyt', 'log', '--format=%H %cI', '--', 'us-states.csv').splitlines()]
out = {}
for f, h in sorted(hist.items()):
    m = re.search(r'(\d\d)-(\d\d)-(\d{4})', f); d = datetime.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    rec = {'first_commit': h[-1][0], 'first_date': h[-1][1], 'n_versions': len(h)}
    for k in (7, 30):
        lim = (d + datetime.timedelta(days=k)).isoformat()
        rec[f'c{k}'] = next((c for c, dt in h if dt <= lim), h[-1][0])
    for key, c in (('first', rec['first_commit']), ('d7', rec['c7']), ('d30', rec['c30'])):
        rec[key] = git('jhu', 'ls-tree', c, '--', f).split()[2]
    rec['final'] = final[f]
    lim = (d + datetime.timedelta(days=7)).isoformat()
    nc = next((c for c, dt in nyt_hist if dt <= lim), None)
    rec['nyt7'] = git('nyt', 'ls-tree', nc, '--', 'us-states.csv').split()[2] if nc else None
    out[d.isoformat()] = rec
json.dump(out, open('work/versions.json', 'w'))
for repo, keys in (('jhu', ('first', 'd7', 'd30', 'final')), ('nyt', ('nyt7',))):
    oids = sorted({r[k] for r in out.values() for k in keys if r[k]})
    git(repo, '-c', 'fetch.negotiationAlgorithm=noop', 'fetch', 'origin', '--no-tags', '--no-write-fetch-head', '--recurse-submodules=no', '--filter=blob:none', '--stdin', inp='\n'.join(oids) + '\n')
    print(repo, len(oids), 'blobs')
print(len(out), 'partitions', min(out), max(out))
