-- Phase 2 raw landing and per-table extraction progress.
-- Apply once to the same PostgreSQL database as the operational source.
BEGIN;

CREATE SCHEMA IF NOT EXISTS zepic_raw;

CREATE TABLE IF NOT EXISTS zepic_raw.extraction_batches (
    batch_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_table TEXT NOT NULL CHECK (source_table IN ('orders', 'order_items', 'inventory')),
    lower_updated_at TIMESTAMPTZ,
    lower_primary_key BIGINT,
    upper_updated_at TIMESTAMPTZ,
    upper_primary_key BIGINT,
    row_count BIGINT NOT NULL CHECK (row_count >= 0),
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    CHECK ((lower_updated_at IS NULL) = (lower_primary_key IS NULL)),
    CHECK ((upper_updated_at IS NULL) = (upper_primary_key IS NULL))
);

CREATE TABLE IF NOT EXISTS zepic_raw.records (
    batch_id BIGINT NOT NULL REFERENCES zepic_raw.extraction_batches(batch_id),
    source_table TEXT NOT NULL CHECK (source_table IN ('orders', 'order_items', 'inventory')),
    source_primary_key BIGINT NOT NULL,
    source_updated_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL,
    extracted_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (batch_id, source_primary_key)
);

CREATE INDEX IF NOT EXISTS idx_raw_records_source_lookup
    ON zepic_raw.records(source_table, source_primary_key, source_updated_at);

CREATE TABLE IF NOT EXISTS zepic_raw.watermarks (
    source_table TEXT PRIMARY KEY CHECK (source_table IN ('orders', 'order_items', 'inventory')),
    last_updated_at TIMESTAMPTZ,
    last_primary_key BIGINT,
    initialized BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((last_updated_at IS NULL) = (last_primary_key IS NULL))
);

COMMIT;
