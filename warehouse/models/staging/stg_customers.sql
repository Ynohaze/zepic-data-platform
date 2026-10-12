with latest as (
    {{ latest_validated_records('customers') }}
)

select
    (payload ->> 'customer_id')::bigint as customer_id,
    payload ->> 'customer_name' as customer_name,
    payload ->> 'customer_tier' as customer_tier,
    (payload ->> 'created_at')::timestamptz as created_at,
    (payload ->> 'updated_at')::timestamptz as updated_at,
    batch_id as raw_batch_id,
    extracted_at
from latest
