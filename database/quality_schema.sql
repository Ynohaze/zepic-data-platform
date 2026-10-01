-- Phase 3 validation runs and quarantined row findings.
-- Safe to apply repeatedly after database/raw_schema.sql.
BEGIN;

CREATE TABLE IF NOT EXISTS zepic_raw.quality_runs (
    batch_id BIGINT NOT NULL REFERENCES zepic_raw.extraction_batches(batch_id),
    rule_version VARCHAR(20) NOT NULL,
    source_table TEXT NOT NULL,
    input_rows BIGINT NOT NULL CHECK (input_rows >= 0),
    valid_rows BIGINT NOT NULL CHECK (valid_rows >= 0),
    quarantined_rows BIGINT NOT NULL CHECK (quarantined_rows >= 0),
    finding_count BIGINT NOT NULL CHECK (finding_count >= 0),
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (batch_id, rule_version),
    CHECK (input_rows = valid_rows + quarantined_rows)
);

CREATE TABLE IF NOT EXISTS zepic_raw.quality_findings (
    batch_id BIGINT NOT NULL,
    source_table TEXT NOT NULL,
    source_primary_key BIGINT NOT NULL,
    rule_version VARCHAR(20) NOT NULL,
    rule_code VARCHAR(80) NOT NULL,
    reason TEXT NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (batch_id, source_table, source_primary_key, rule_version, rule_code),
    FOREIGN KEY (batch_id, rule_version)
        REFERENCES zepic_raw.quality_runs(batch_id, rule_version) ON DELETE CASCADE,
    FOREIGN KEY (batch_id, source_primary_key)
        REFERENCES zepic_raw.records(batch_id, source_primary_key) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_quality_findings_source
    ON zepic_raw.quality_findings(source_table, source_primary_key, rule_code);

CREATE OR REPLACE VIEW zepic_raw.quarantined_records AS
SELECT r.*, q.rule_version, f.rule_code, f.reason, f.detected_at
FROM zepic_raw.records r
JOIN LATERAL (
    SELECT rule_version
    FROM zepic_raw.quality_runs
    WHERE batch_id = r.batch_id
    ORDER BY completed_at DESC, rule_version DESC
    LIMIT 1
) q ON TRUE
JOIN zepic_raw.quality_findings f
 ON f.batch_id = r.batch_id
 AND f.source_table = r.source_table
 AND f.source_primary_key = r.source_primary_key
 AND f.rule_version = q.rule_version;

CREATE OR REPLACE VIEW zepic_raw.validated_records AS
SELECT r.*
FROM zepic_raw.records r
JOIN LATERAL (
    SELECT rule_version
    FROM zepic_raw.quality_runs
    WHERE batch_id = r.batch_id
    ORDER BY completed_at DESC, rule_version DESC
    LIMIT 1
) q ON TRUE
LEFT JOIN zepic_raw.quality_findings f
  ON f.batch_id = r.batch_id
 AND f.source_table = r.source_table
 AND f.source_primary_key = r.source_primary_key
 AND f.rule_version = q.rule_version
WHERE f.batch_id IS NULL;

COMMIT;
