select
    order_id,
    order_total_amount,
    calculated_line_amount
from {{ ref('fct_orders') }}
where calculated_line_amount is null
   or order_total_amount <> calculated_line_amount
