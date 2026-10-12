select
    item.order_item_id,
    item.order_id,
    orders.customer_id,
    item.product_id,
    orders.order_status,
    orders.created_at as order_created_at,
    orders.created_at::date as order_date,
    orders.updated_at as order_last_updated_at,
    item.quantity,
    item.unit_price,
    (item.quantity * item.unit_price)::numeric(20, 2) as line_amount
from {{ ref('stg_order_items') }} item
inner join {{ ref('stg_orders') }} orders using (order_id)
