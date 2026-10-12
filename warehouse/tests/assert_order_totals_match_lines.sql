with item_totals as (
    select
        order_id,
        sum(quantity * unit_price)::numeric(20, 2) as calculated_total
    from {{ ref('stg_order_items') }}
    group by order_id
)

select
    orders.order_id,
    orders.order_total_amount,
    item_totals.calculated_total
from {{ ref('stg_orders') }} orders
left join item_totals using (order_id)
where item_totals.order_id is null
   or orders.order_total_amount <> item_totals.calculated_total
