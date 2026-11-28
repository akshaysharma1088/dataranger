# Control totals from a flat file's own trailer record

Two small delivery files, one delimited and one fixed-width, each with its own trailer line
stating the delivery's row count and total amount (issue #86). The trailer is often stronger
evidence than a totals table computed downstream, because the sender wrote it.

`payments_20261101.csv` is comma-delimited: two detail rows (`DET,...`) and a trailer row
(`TRL,2,35.50`) giving the record count and amount.

```bash
dataranger totals --from-trailer examples/trailers/payments_20261101.csv --trailer-date 2026-11-01 \
    --trailer-record-type-field 0 --trailer-record-type-value TRL \
    --trailer-count-field 1 --trailer-amount-field 2 --out control_totals.csv
```

`payments_fixed_20261101.txt` is fixed-width: a 3-character record type (`DET`/`TRL`), then a
6-character count field, then an amount field. Use `start:end` character slices instead of plain
field indexes:

```bash
dataranger totals --from-trailer examples/trailers/payments_fixed_20261101.txt --trailer-date 2026-11-01 \
    --trailer-record-type-field 0:3 --trailer-record-type-value TRL \
    --trailer-count-field 3:9 --trailer-amount-field 9:20 --out control_totals.csv
```

Both commands write one control-total row (`2026-11-01`, segment `*`, `n=2`, `amount=35.50`) to
`control_totals.csv`. Add `--compare-to computed_totals.csv` to also check the trailer's own
numbers against totals computed from the landed rows; see `tests/test_trailer_totals.py` for a
worked example of that check, including what a disagreement looks like.
