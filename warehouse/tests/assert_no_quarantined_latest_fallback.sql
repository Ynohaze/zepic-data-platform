with staged_keys as (
    select 'customers' as source_table, customer_id as source_primary_key from {{ ref('stg_customers') }}
    union all
    select 'products', product_id from {{ ref('stg_products') }}
    union all
    select 'machines', machine_id from {{ ref('stg_machines') }}
    union all
    select 'inventory', product_id from {{ ref('stg_inventory') }}
    union all
    select 'orders', order_id from {{ ref('stg_orders') }}
    union all
    select 'order_items', order_item_id from {{ ref('stg_order_items') }}
    union all
    select 'production_orders', production_order_id from {{ ref('stg_production_orders') }}
    union all
    select 'production_runs', production_run_id from {{ ref('stg_production_runs') }}
    union all
    select 'maintenance_records', maintenance_record_id from {{ ref('stg_maintenance_records') }}
),

ranked_checked_raw as (
    select
        raw.batch_id,
        raw.source_table,
        raw.source_primary_key,
        validated.batch_id is not null as is_valid,
        row_number() over (
            partition by raw.source_table, raw.source_primary_key
            order by raw.source_updated_at desc, raw.batch_id desc
        ) as version_rank
    from {{ source('zepic_raw', 'records') }} raw
    left join {{ source('zepic_raw', 'validated_records') }} validated
        on validated.batch_id = raw.batch_id
        and validated.source_table = raw.source_table
        and validated.source_primary_key = raw.source_primary_key
    where exists (
        select 1
        from {{ source('zepic_raw', 'quality_runs') }} quality_run
        where quality_run.batch_id = raw.batch_id
    )
)

select
    latest.source_table,
    latest.source_primary_key,
    latest.batch_id
from ranked_checked_raw latest
inner join staged_keys using (source_table, source_primary_key)
where latest.version_rank = 1
  and not latest.is_valid
