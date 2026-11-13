-- What the source system says it sent: one row per day and channel, plus a '*' row for the whole day.
select txn_date as paid_on, channel, n, amount
from {{ ref('raw_control_totals') }}
