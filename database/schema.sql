-- Zepic Phase 1.1 agreed schema; initialize an empty PostgreSQL 16 database.
-- Business transactions (shipping, inventory and maintenance state) belong to the simulator.
BEGIN;

CREATE TABLE customers (
    customer_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    customer_name VARCHAR(200) NOT NULL,
    customer_tier VARCHAR(20) NOT NULL
        CHECK (customer_tier IN ('standard', 'silver', 'gold')),

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE products (
    product_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    sku VARCHAR(50) NOT NULL UNIQUE,
    product_name VARCHAR(200) NOT NULL,

    unit_price NUMERIC(12,2) NOT NULL
        CHECK (unit_price >= 0),

    batch_multiple INTEGER NOT NULL
        CHECK (batch_multiple > 0),

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE machines (
    machine_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    machine_code VARCHAR(50) NOT NULL UNIQUE,
    machine_name VARCHAR(200) NOT NULL,

    status VARCHAR(20) NOT NULL DEFAULT 'available'
        CHECK (
            status IN (
                'available',
                'maintenance',
                'retired'
            )
        ),

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE inventory (
    product_id BIGINT PRIMARY KEY
        REFERENCES products(product_id),

    available_quantity INTEGER NOT NULL DEFAULT 0
        CHECK (available_quantity >= 0),

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE orders (
    order_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    customer_id BIGINT NOT NULL
        REFERENCES customers(customer_id),

    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (
            status IN (
                'pending',
                'confirmed',
                'shipped'
            )
        ),

    total_amount NUMERIC(12,2) NOT NULL CHECK (total_amount >= 0),

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE order_items (
    order_item_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    order_id BIGINT NOT NULL
        REFERENCES orders(order_id),

    product_id BIGINT NOT NULL
        REFERENCES products(product_id),

    quantity INTEGER NOT NULL
        CHECK (quantity > 0),

    unit_price NUMERIC(12,2) NOT NULL
        CHECK (unit_price >= 0),

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (order_id, product_id)
);

CREATE TABLE production_orders (
    production_order_id BIGINT
        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    product_id BIGINT NOT NULL
        REFERENCES products(product_id),

    shortage_quantity INTEGER NOT NULL
        CHECK (shortage_quantity > 0),

    batch_multiple INTEGER NOT NULL
        CHECK (batch_multiple > 0),

    buffer_rate NUMERIC(6,4) NOT NULL DEFAULT 0.05
        CHECK (
            buffer_rate >= 0
            AND buffer_rate < 1
        ),

    planned_quantity INTEGER NOT NULL
        CHECK (planned_quantity > 0),

    planned_start_date DATE NOT NULL,

    status VARCHAR(20) NOT NULL DEFAULT 'planned'
        CHECK (
            status IN (
                'planned',
                'released',
                'completed'
            )
        ),

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CHECK (
        MOD(planned_quantity, batch_multiple) = 0
    )
);

CREATE TABLE production_runs (
    production_run_id BIGINT
        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    production_order_id BIGINT NOT NULL
        REFERENCES production_orders(production_order_id),

    machine_id BIGINT NOT NULL
        REFERENCES machines(machine_id),

    start_time TIMESTAMPTZ NOT NULL,

    end_time TIMESTAMPTZ,

    actual_quantity INTEGER
        CHECK (
            actual_quantity IS NULL
            OR actual_quantity >= 0
        ),

    status VARCHAR(20) NOT NULL DEFAULT 'running'
        CHECK (
            status IN (
                'running',
                'completed',
                'failed'
            )
        ),

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CHECK (
        end_time IS NULL
        OR end_time >= start_time
    ),

    CHECK (
        (
            status = 'running'
            AND end_time IS NULL
            AND actual_quantity IS NULL
        )
        OR
        (
            status IN ('completed', 'failed')
            AND end_time IS NOT NULL
            AND actual_quantity IS NOT NULL
        )
    )
);

CREATE TABLE maintenance_records (
    maintenance_record_id BIGINT
        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    machine_id BIGINT NOT NULL
        REFERENCES machines(machine_id),

    maintenance_type VARCHAR(20) NOT NULL
        CHECK (
            maintenance_type IN (
                'preventive',
                'corrective'
            )
        ),

    issue_description TEXT,

    start_time TIMESTAMPTZ,
    end_time TIMESTAMPTZ,

    maintenance_cost NUMERIC(12,2)
        CHECK (
            maintenance_cost IS NULL
            OR maintenance_cost >= 0
        ),

    status VARCHAR(20) NOT NULL DEFAULT 'scheduled'
        CHECK (
            status IN (
                'scheduled',
                'in_progress',
                'completed'
            )
        ),

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CHECK (
        end_time IS NULL
        OR end_time >= start_time
    ),

    CHECK (
        (
            status = 'scheduled'
            AND start_time IS NULL
            AND end_time IS NULL
        )
        OR
        (
            status = 'in_progress'
            AND start_time IS NOT NULL
            AND end_time IS NULL
        )
        OR
        (
            status = 'completed'
            AND start_time IS NOT NULL
            AND end_time IS NOT NULL
            AND maintenance_cost IS NOT NULL
        )
    )
);

-- Operational query indexes and machine activity invariants.
CREATE INDEX idx_orders_customer_id
    ON orders(customer_id);

CREATE INDEX idx_orders_status
    ON orders(status);

CREATE INDEX idx_order_items_order_id
    ON order_items(order_id);

CREATE INDEX idx_order_items_product_id
    ON order_items(product_id);

CREATE INDEX idx_production_orders_product_status
    ON production_orders(product_id, status);

CREATE INDEX idx_production_runs_production_order_id
    ON production_runs(production_order_id);

CREATE INDEX idx_production_runs_machine_id
    ON production_runs(machine_id);

CREATE INDEX idx_maintenance_records_machine_id
    ON maintenance_records(machine_id);

CREATE INDEX idx_maintenance_records_status
    ON maintenance_records(status);

CREATE UNIQUE INDEX uq_machine_active_production_run
    ON production_runs(machine_id)
    WHERE status = 'running';

CREATE UNIQUE INDEX uq_machine_active_maintenance
    ON maintenance_records(machine_id)
    WHERE status = 'in_progress';

-- Watermark extraction indexes.
CREATE INDEX idx_customers_updated_at
    ON customers(updated_at);

CREATE INDEX idx_products_updated_at
    ON products(updated_at);

CREATE INDEX idx_inventory_updated_at
    ON inventory(updated_at);

CREATE INDEX idx_orders_updated_at
    ON orders(updated_at);

CREATE INDEX idx_order_items_updated_at
    ON order_items(updated_at);

CREATE INDEX idx_production_orders_updated_at
    ON production_orders(updated_at);

CREATE INDEX idx_production_runs_updated_at
    ON production_runs(updated_at);

CREATE INDEX idx_machines_updated_at
    ON machines(updated_at);

CREATE INDEX idx_maintenance_records_updated_at
    ON maintenance_records(updated_at);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.updated_at IS NOT DISTINCT FROM OLD.updated_at THEN
        NEW.updated_at = CURRENT_TIMESTAMP;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_customers_updated_at
BEFORE UPDATE ON customers
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_products_updated_at
BEFORE UPDATE ON products
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_machines_updated_at
BEFORE UPDATE ON machines
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_inventory_updated_at
BEFORE UPDATE ON inventory
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_orders_updated_at
BEFORE UPDATE ON orders
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_order_items_updated_at
BEFORE UPDATE ON order_items
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_production_orders_updated_at
BEFORE UPDATE ON production_orders
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_production_runs_updated_at
BEFORE UPDATE ON production_runs
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_maintenance_records_updated_at
BEFORE UPDATE ON maintenance_records
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE OR REPLACE FUNCTION validate_machine_for_production()
RETURNS TRIGGER AS $$
DECLARE
    machine_status VARCHAR(20);
BEGIN
    SELECT status INTO machine_status
    FROM machines
    WHERE machine_id = NEW.machine_id;

    IF machine_status <> 'available' THEN
        RAISE EXCEPTION 'Machine % is not available for production', NEW.machine_id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_validate_machine_for_production
BEFORE INSERT OR UPDATE OF machine_id, status ON production_runs
FOR EACH ROW
WHEN (NEW.status = 'running')
EXECUTE FUNCTION validate_machine_for_production();

COMMIT;
