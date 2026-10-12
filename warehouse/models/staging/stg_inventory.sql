with latest as (
    {{ latest_validated_records('inventory') }}
)

select
    (payload ->> 'product_id')::bigint as product_id,
    (payload ->> 'available_quantity')::integer as available_quantity,
    (payload ->> 'created_at')::timestamptz as created_at,
    (payload ->> 'updated_at')::timestamptz as updated_at,
    batch_id as raw_batch_id,
    extracted_at
from latest
