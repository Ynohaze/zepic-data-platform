with latest as (
    {{ latest_validated_records('maintenance_records') }}
)

select
    (payload ->> 'maintenance_record_id')::bigint as maintenance_record_id,
    (payload ->> 'machine_id')::bigint as machine_id,
    payload ->> 'maintenance_type' as maintenance_type,
    payload ->> 'issue_description' as issue_description,
    (payload ->> 'start_time')::timestamptz as start_time,
    (payload ->> 'end_time')::timestamptz as end_time,
    (payload ->> 'maintenance_cost')::numeric(12, 2) as maintenance_cost,
    payload ->> 'status' as maintenance_status,
    (payload ->> 'created_at')::timestamptz as created_at,
    (payload ->> 'updated_at')::timestamptz as updated_at,
    batch_id as raw_batch_id,
    extracted_at
from latest
