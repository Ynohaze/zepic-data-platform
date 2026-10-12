with latest as (
    {{ latest_validated_records('machines') }}
)

select
    (payload ->> 'machine_id')::bigint as machine_id,
    payload ->> 'machine_code' as machine_code,
    payload ->> 'machine_name' as machine_name,
    payload ->> 'status' as machine_status,
    (payload ->> 'created_at')::timestamptz as created_at,
    (payload ->> 'updated_at')::timestamptz as updated_at,
    batch_id as raw_batch_id,
    extracted_at
from latest
