# Real revisions of a public feed (paper, Section 6.9)

The Johns Hopkins CSSE COVID-19 repository keeps every daily file as first published and as later corrected.
These scripts rebuild the study from two public repositories. Nothing from them is redistributed here except the
150 hand-adjudicated state-days in `results/real_feed/adjudication_labels.csv` (values from the JHU repository, CC BY 4.0).

    mkdir real_feed_work && cd real_feed_work && mkdir work
    git clone --filter=blob:none --no-checkout --single-branch --branch master https://github.com/CSSEGISandData/COVID-19 jhu
    git clone --filter=blob:none --no-checkout --single-branch https://github.com/nytimes/covid-19-data nyt
    python ../benchmarks/real_feed/01_fetch.py      # the versions of every partition, and NYT totals as of patrol time
    python ../benchmarks/real_feed/02_build.py      # work/state_days.csv.gz
    python ../benchmarks/real_feed/03_gate.py 0.01  # work/gate_summary_0.01.json (also 0.005 and 0.02 for sensitivity)
    python ../benchmarks/real_feed/04_sheet.py      # the adjudication sample, before labels (needs work/jhu_readme.md, see below)
    python ../benchmarks/real_feed/05_adjudicated.py ../results/real_feed/adjudication_labels.csv

`04_sheet.py` reads the publisher's modification log: `git -C jhu show HEAD:csse_covid_19_data/README.md > work/jhu_readme.md`.

Design. Warehouse copy: a state-day's cases and deaths as first published. Patrol at day 7: keep the copy or reload it from the
source as it then stands. Evidence: the gap to the New York Times state totals as published at day 7. Ground truth: the source at
day 30; a value more than 1% away is wrong. Thresholds are certified on 2020 and 2021 and tested on 2022 and 2023.
Labels: `error`, `restatement` or `unclear`, assigned by the author against the publisher's modification log.
