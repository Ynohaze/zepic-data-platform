select
    item.order_item_id,
    item.order_id,
    orders.customer_id,
    item.product_id,
    orders.order_status,
    orders.order_created_at,
    orders.order_date,
    orders.order_last_updated_at,
    item.quantity,
    item.unit_price,
    (item.quantity * item.unit_price)::numeric(20, 2) as line_amount
from {{ ref('stg_order_items') }} item
inner join {{ ref('fct_orders') }} orders using (order_id)
