select product_id, available_quantity
from {{ ref('current_inventory') }}
where available_quantity < 0
