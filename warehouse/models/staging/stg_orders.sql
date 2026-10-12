with latest as (
    {{ latest_validated_records('orders') }}
)

select
    (payload ->> 'order_id')::bigint as order_id,
    (payload ->> 'customer_id')::bigint as customer_id,
    payload ->> 'status' as order_status,
    (payload ->> 'total_amount')::numeric(12, 2) as order_total_amount,
    (payload ->> 'created_at')::timestamptz as created_at,
    (payload ->> 'updated_at')::timestamptz as updated_at,
    batch_id as raw_batch_id,
    extracted_at
from latest
