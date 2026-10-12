select
    machine_id,
    machine_code,
    machine_name,
    machine_status,
    created_at,
    updated_at
from {{ ref('stg_machines') }}
