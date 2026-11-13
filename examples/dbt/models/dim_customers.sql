select customer_id, segment, state
from {{ ref('raw_customers') }}
