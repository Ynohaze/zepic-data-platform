select
    batch_id as raw_batch_id,
    source_primary_key as order_id,
    (payload ->> 'customer_id')::bigint as customer_id,
    payload ->> 'status' as order_status,
    (payload ->> 'total_amount')::numeric(12, 2) as order_total_amount,
    (payload ->> 'created_at')::timestamptz as created_at,
    (payload ->> 'updated_at')::timestamptz as status_observed_at,
    source_updated_at,
    extracted_at
from {{ source('zepic_raw', 'validated_records') }}
where source_table = 'orders'
