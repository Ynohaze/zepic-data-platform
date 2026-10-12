select
    maintenance_record_id,
    machine_id,
    maintenance_type,
    maintenance_status,
    issue_description,
    start_time,
    end_time,
    maintenance_cost,
    case
        when end_time is not null
            then extract(epoch from (end_time - start_time)) / 60.0
    end::numeric(12, 2) as maintenance_duration_minutes,
    created_at,
    updated_at
from {{ ref('stg_maintenance_records') }}
