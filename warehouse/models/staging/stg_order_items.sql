with latest as (
    {{ latest_validated_records('order_items') }}
)

select
    (payload ->> 'order_item_id')::bigint as order_item_id,
    (payload ->> 'order_id')::bigint as order_id,
    (payload ->> 'product_id')::bigint as product_id,
    (payload ->> 'quantity')::integer as quantity,
    (payload ->> 'unit_price')::numeric(12, 2) as unit_price,
    (payload ->> 'created_at')::timestamptz as created_at,
    (payload ->> 'updated_at')::timestamptz as updated_at,
    batch_id as raw_batch_id,
    extracted_at
from latest
