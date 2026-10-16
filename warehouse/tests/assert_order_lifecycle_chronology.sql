select
    order_id,
    order_created_at,
    pending_at,
    confirmed_at,
    shipped_at
from {{ ref('fct_orders') }}
where (pending_at is not null and pending_at < order_created_at)
   or (confirmed_at is not null and confirmed_at < order_created_at)
   or (shipped_at is not null and shipped_at < order_created_at)
   or (pending_at is not null and confirmed_at is not null and confirmed_at < pending_at)
   or (confirmed_at is not null and shipped_at is not null and shipped_at < confirmed_at)
