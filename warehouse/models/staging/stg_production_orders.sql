with latest as (
    {{ latest_validated_records('production_orders') }}
)

select
    (payload ->> 'production_order_id')::bigint as production_order_id,
    (payload ->> 'product_id')::bigint as product_id,
    (payload ->> 'shortage_quantity')::integer as shortage_quantity,
    (payload ->> 'batch_multiple')::integer as batch_multiple,
    (payload ->> 'buffer_rate')::numeric(6, 4) as buffer_rate,
    (payload ->> 'planned_quantity')::integer as planned_quantity,
    (payload ->> 'planned_start_date')::date as planned_start_date,
    payload ->> 'status' as production_order_status,
    (payload ->> 'created_at')::timestamptz as created_at,
    (payload ->> 'updated_at')::timestamptz as updated_at,
    batch_id as raw_batch_id,
    extracted_at
from latest
