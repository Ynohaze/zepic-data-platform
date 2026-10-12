select
    customer_id,
    customer_name,
    customer_tier,
    created_at,
    updated_at
from {{ ref('stg_customers') }}
