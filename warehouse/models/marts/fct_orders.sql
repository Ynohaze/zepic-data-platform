with item_metrics as (
    select
        order_id,
        count(*) as line_count,
        sum(quantity) as unit_count,
        sum(quantity * unit_price)::numeric(20, 2) as calculated_line_amount
    from {{ ref('stg_order_items') }}
    group by order_id
),

status_milestones as (
    select
        order_id,
        bool_or(order_status = 'pending') as pending_status_observed,
        bool_or(order_status = 'confirmed') as confirmed_status_observed,
        bool_or(order_status = 'shipped') as shipped_status_observed,
        min(status_observed_at) filter (where order_status = 'confirmed') as confirmed_at,
        min(status_observed_at) filter (where order_status = 'shipped') as shipped_at
    from {{ ref('stg_order_versions') }}
    group by order_id
)

select
    orders.order_id,
    orders.customer_id,
    orders.order_status,
    orders.order_total_amount,
    items.line_count,
    items.unit_count,
    items.calculated_line_amount,
    orders.created_at as order_created_at,
    orders.created_at::date as order_date,
    orders.created_at as pending_at,
    milestones.confirmed_at,
    milestones.shipped_at,
    coalesce(milestones.pending_status_observed, false) as pending_status_observed,
    coalesce(milestones.confirmed_status_observed, false) as confirmed_status_observed,
    coalesce(milestones.shipped_status_observed, false) as shipped_status_observed,
    (
        coalesce(milestones.pending_status_observed, false)
        and coalesce(milestones.confirmed_status_observed, false)
        and coalesce(milestones.shipped_status_observed, false)
    ) as lifecycle_fully_observed,
    case
        when milestones.confirmed_at is not null
            then extract(epoch from (milestones.confirmed_at - orders.created_at)) / 60.0
    end::numeric(12, 2) as minutes_to_confirmation,
    case
        when milestones.shipped_at is not null
            then extract(epoch from (milestones.shipped_at - orders.created_at)) / 60.0
    end::numeric(12, 2) as minutes_to_shipment,
    case
        when milestones.confirmed_at is not null and milestones.shipped_at is not null
            then extract(epoch from (milestones.shipped_at - milestones.confirmed_at)) / 60.0
    end::numeric(12, 2) as minutes_confirmation_to_shipment,
    orders.updated_at as order_last_updated_at,
    orders.raw_batch_id
from {{ ref('stg_orders') }} orders
left join item_metrics items using (order_id)
left join status_milestones milestones using (order_id)
