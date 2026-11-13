select
    txn_id as payment_id,
    customer_id,
    txn_date as paid_on,
    channel,
    category,
    amount
from {{ ref('raw_transactions') }}
