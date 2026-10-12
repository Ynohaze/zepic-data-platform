select
    product_id,
    sku,
    product_name,
    current_unit_price,
    batch_multiple,
    is_active,
    created_at,
    updated_at
from {{ ref('stg_products') }}
