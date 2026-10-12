with item_metrics as (
    select
        order_id,
        count(*) as line_count,
        sum(quantity) as unit_count
    from {{ ref('stg_order_items') }}
    group by order_id
)

select
    orders.created_at::date as order_date,
    orders.order_status,
    count(*) as order_count,
    sum(items.line_count) as line_count,
    sum(items.unit_count) as unit_count,
    sum(orders.order_total_amount)::numeric(20, 2) as order_amount
from {{ ref('stg_orders') }} orders
inner join item_metrics items using (order_id)
group by orders.created_at::date, orders.order_status
