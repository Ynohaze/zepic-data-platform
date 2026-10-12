select
    inventory.product_id,
    products.sku,
    products.product_name,
    inventory.available_quantity,
    products.current_unit_price,
    (inventory.available_quantity * products.current_unit_price)::numeric(20, 2) as inventory_retail_value,
    inventory.updated_at as inventory_updated_at
from {{ ref('stg_inventory') }} inventory
inner join {{ ref('dim_products') }} products using (product_id)
