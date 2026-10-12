select
    runs.production_run_id,
    runs.production_order_id,
    orders.product_id,
    runs.machine_id,
    orders.planned_start_date,
    orders.shortage_quantity,
    orders.batch_multiple,
    orders.buffer_rate,
    orders.planned_quantity,
    runs.actual_quantity,
    orders.production_order_status,
    runs.production_run_status,
    runs.start_time,
    runs.end_time,
    case
        when runs.end_time is not null
            then extract(epoch from (runs.end_time - runs.start_time)) / 60.0
    end::numeric(12, 2) as run_duration_minutes,
    case
        when runs.actual_quantity is not null
            then runs.actual_quantity - orders.planned_quantity
    end as quantity_variance
from {{ ref('stg_production_runs') }} runs
inner join {{ ref('stg_production_orders') }} orders using (production_order_id)
