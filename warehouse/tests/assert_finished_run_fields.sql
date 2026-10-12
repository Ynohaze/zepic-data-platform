select production_run_id
from {{ ref('fct_production_runs') }}
where (production_run_status = 'running' and (end_time is not null or actual_quantity is not null))
   or (production_run_status in ('completed', 'failed') and (end_time is null or actual_quantity is null))
