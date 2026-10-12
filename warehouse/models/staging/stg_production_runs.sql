with latest as (
    {{ latest_validated_records('production_runs') }}
)

select
    (payload ->> 'production_run_id')::bigint as production_run_id,
    (payload ->> 'production_order_id')::bigint as production_order_id,
    (payload ->> 'machine_id')::bigint as machine_id,
    (payload ->> 'start_time')::timestamptz as start_time,
    (payload ->> 'end_time')::timestamptz as end_time,
    (payload ->> 'actual_quantity')::integer as actual_quantity,
    payload ->> 'status' as production_run_status,
    (payload ->> 'created_at')::timestamptz as created_at,
    (payload ->> 'updated_at')::timestamptz as updated_at,
    batch_id as raw_batch_id,
    extracted_at
from latest
