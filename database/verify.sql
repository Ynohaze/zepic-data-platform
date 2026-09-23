-- Run against a disposable database: rows roll back, identity sequences do not.
\set ON_ERROR_STOP on
BEGIN;

CREATE FUNCTION pg_temp.expect_error(statement TEXT, expected_state TEXT)
RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE actual_state TEXT;
BEGIN
    BEGIN
        EXECUTE statement;
    EXCEPTION WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS actual_state = RETURNED_SQLSTATE;
        IF actual_state = expected_state THEN RETURN; END IF;
        RAISE EXCEPTION 'Expected %, got %: %', expected_state, actual_state, statement;
    END;
    RAISE EXCEPTION 'Statement unexpectedly succeeded: %', statement;
END;
$$;

DO $$
DECLARE n INTEGER;
BEGIN
    SELECT count(*) INTO n FROM pg_tables WHERE schemaname = 'public';
    IF n <> 9 THEN RAISE EXCEPTION 'Expected 9 tables, got %', n; END IF;
    SELECT count(*) INTO n FROM pg_indexes WHERE schemaname = 'public';
    IF n <> 32 THEN RAISE EXCEPTION 'Expected 32 indexes including PK/UNIQUE, got %', n; END IF;
    SELECT count(*) INTO n FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
    JOIN pg_namespace s ON s.oid = c.relnamespace
    WHERE s.nspname = 'public' AND NOT t.tgisinternal;
    IF n <> 10 THEN RAISE EXCEPTION 'Expected 10 triggers, got %', n; END IF;
END;
$$;

INSERT INTO customers (customer_name, customer_tier, updated_at)
VALUES ('Verification customer', 'standard', '2000-01-01Z') RETURNING customer_id AS cid \gset
INSERT INTO products (sku, product_name, unit_price, batch_multiple)
VALUES ('VERIFY-P', 'Verification product', 10, 20) RETURNING product_id AS pid \gset
INSERT INTO machines (machine_code, machine_name)
VALUES ('VERIFY-M', 'Verification machine') RETURNING machine_id AS mid \gset
INSERT INTO inventory (product_id) VALUES (:pid);
INSERT INTO orders (customer_id, total_amount) VALUES (:cid, 20) RETURNING order_id AS oid \gset
INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES (:oid, :pid, 2, 10);
INSERT INTO production_orders (product_id, shortage_quantity, batch_multiple, planned_quantity, planned_start_date)
VALUES (:pid, 10, 20, 20, CURRENT_DATE) RETURNING production_order_id AS poid \gset

SELECT pg_temp.expect_error('UPDATE inventory SET available_quantity = -1', '23514');
SELECT pg_temp.expect_error('UPDATE customers SET customer_tier = ''invalid''', '23514');
SELECT pg_temp.expect_error('UPDATE orders SET total_amount = -1', '23514');
SELECT pg_temp.expect_error('UPDATE order_items SET quantity = 0', '23514');
SELECT pg_temp.expect_error('UPDATE production_orders SET planned_quantity = 21', '23514');
SELECT pg_temp.expect_error('UPDATE production_orders SET shortage_quantity = 0', '23514');
SELECT pg_temp.expect_error('UPDATE production_orders SET buffer_rate = 1', '23514');
SELECT pg_temp.expect_error('INSERT INTO orders(customer_id, total_amount) VALUES (-1, 0)', '23503');
SELECT pg_temp.expect_error(format('INSERT INTO order_items(order_id, product_id, quantity, unit_price) VALUES (%s,%s,1,10)', :oid, :pid), '23505');

UPDATE customers SET customer_name = 'Updated customer' WHERE customer_id = :cid;
DO $$ BEGIN
    IF EXISTS (SELECT FROM customers WHERE customer_name = 'Updated customer' AND updated_at <> CURRENT_TIMESTAMP)
    THEN RAISE EXCEPTION 'Automatic updated_at failed'; END IF;
END $$;
UPDATE customers SET updated_at = '2001-01-01Z' WHERE customer_id = :cid;
DO $$ BEGIN
    IF EXISTS (SELECT FROM customers WHERE customer_name = 'Updated customer' AND updated_at <> '2001-01-01Z')
    THEN RAISE EXCEPTION 'Explicit simulation timestamp was overwritten'; END IF;
END $$;

UPDATE machines SET status = 'maintenance' WHERE machine_id = :mid;
SELECT pg_temp.expect_error(format('INSERT INTO production_runs(production_order_id,machine_id,start_time) VALUES (%s,%s,CURRENT_TIMESTAMP)', :poid, :mid), 'P0001');
UPDATE machines SET status = 'retired' WHERE machine_id = :mid;
SELECT pg_temp.expect_error(format('INSERT INTO production_runs(production_order_id,machine_id,start_time) VALUES (%s,%s,CURRENT_TIMESTAMP)', :poid, :mid), 'P0001');
UPDATE machines SET status = 'available' WHERE machine_id = :mid;
INSERT INTO production_runs (production_order_id, machine_id, start_time)
VALUES (:poid, :mid, CURRENT_TIMESTAMP) RETURNING production_run_id AS rid \gset
SELECT pg_temp.expect_error(format('INSERT INTO production_runs(production_order_id,machine_id,start_time) VALUES (%s,%s,CURRENT_TIMESTAMP)', :poid, :mid), '23505');
SELECT pg_temp.expect_error('UPDATE production_runs SET status = ''completed''', '23514');
UPDATE production_runs SET status = 'failed', actual_quantity = 0, end_time = CURRENT_TIMESTAMP WHERE production_run_id = :rid;
INSERT INTO production_runs (production_order_id, machine_id, start_time)
VALUES (:poid, :mid, CURRENT_TIMESTAMP);
UPDATE production_runs SET status = 'completed', actual_quantity = 20, end_time = CURRENT_TIMESTAMP WHERE status = 'running';

INSERT INTO maintenance_records (machine_id, maintenance_type, status, start_time)
VALUES (:mid, 'preventive', 'in_progress', CURRENT_TIMESTAMP) RETURNING maintenance_record_id AS mrid \gset
SELECT pg_temp.expect_error(format('INSERT INTO maintenance_records(machine_id,maintenance_type,status,start_time) VALUES (%s,''corrective'',''in_progress'',CURRENT_TIMESTAMP)', :mid), '23505');
SELECT pg_temp.expect_error('UPDATE maintenance_records SET status = ''completed'', end_time = CURRENT_TIMESTAMP', '23514');
UPDATE maintenance_records SET status = 'completed', end_time = CURRENT_TIMESTAMP, maintenance_cost = 0 WHERE maintenance_record_id = :mrid;

ROLLBACK;
\echo 'PASS: schema objects, constraints, machine checks and timestamp behavior'
