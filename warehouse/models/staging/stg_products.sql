with latest as (
    {{ latest_validated_records('products') }}
)

select
    (payload ->> 'product_id')::bigint as product_id,
    payload ->> 'sku' as sku,
    payload ->> 'product_name' as product_name,
    (payload ->> 'unit_price')::numeric(12, 2) as current_unit_price,
    (payload ->> 'batch_multiple')::integer as batch_multiple,
    (payload ->> 'is_active')::boolean as is_active,
    (payload ->> 'created_at')::timestamptz as created_at,
    (payload ->> 'updated_at')::timestamptz as updated_at,
    batch_id as raw_batch_id,
    extracted_at
from latest
