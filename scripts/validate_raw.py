"""Validate landed raw batches; quarantine findings without changing raw rows or watermarks."""

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RULE_VERSION = "v1"
TABLES = {
    "customers": ("customer_id",),
    "products": ("product_id",),
    "machines": ("machine_id",),
    "inventory": ("product_id",),
    "orders": ("order_id",),
    "order_items": ("order_item_id",),
    "production_orders": ("production_order_id",),
    "production_runs": ("production_run_id",),
    "maintenance_records": ("maintenance_record_id",),
}
MONEY_LIMIT = Decimal("10000000000")


def parse_timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def parse_decimal(value):
    if not isinstance(value, str):
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def validate_record(table, primary_key, source_updated_at, payload, references):
    """Return stable (rule code, reason) pairs for one immutable raw row."""
    findings = []

    def bad(code, reason):
        findings.append((code, reason))

    def required_string(field):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            bad(f"{field}_required", f"{field} must be a non-empty string")

    def enum(field, allowed, rule_code, label):
        value = payload.get(field)
        if not isinstance(value, str) or value not in allowed:
            bad(rule_code, f"{label} is outside the allowed values")

    def integer(field, minimum=None):
        value = payload.get(field)
        if type(value) is not int or (minimum is not None and value < minimum):
            tail = " an integer" if minimum is None else f" an integer >= {minimum}"
            bad(f"{field}_invalid", f"{field} must be{tail}")
            return None
        return value

    def decimal(field, nullable=False):
        value = payload.get(field)
        if value is None and nullable:
            return None
        number = parse_decimal(value)
        try:
            valid_scale = number is not None and number.quantize(Decimal("0.01")) == number
        except InvalidOperation:
            valid_scale = False
        if number is None or number < 0 or number >= MONEY_LIMIT or not valid_scale:
            bad(f"{field}_invalid", f"{field} must be a decimal string in [0, 10000000000) with at most 2 decimals")
            return None
        return number

    def fk(field, target):
        value = integer(field, minimum=1)
        if value is not None and value not in references.get(target, set()):
            bad(f"{field}_missing", f"{field} does not reference a landed {target} row")

    def timestamp(field, nullable=False):
        value = payload.get(field)
        if value is None and nullable:
            return None
        parsed = parse_timestamp(value)
        if parsed is None:
            bad(f"{field}_invalid", f"{field} must be a timezone-aware timestamp")
        return parsed

    if not isinstance(payload, dict):
        return [("payload_not_object", "payload must be a JSON object")]

    key_field = TABLES[table][0]
    if type(payload.get(key_field)) is not int or payload[key_field] != primary_key:
        bad("primary_key_mismatch", f"payload {key_field} must match source_primary_key")
    created_at = timestamp("created_at")
    updated_at = timestamp("updated_at")
    if updated_at is not None and updated_at != source_updated_at.astimezone(timezone.utc):
        bad("updated_at_mismatch", "payload updated_at must match source_updated_at")

    if table == "customers":
        required_string("customer_name")
        enum("customer_tier", {"standard", "silver", "gold"}, "customer_tier_invalid", "customer_tier")
    elif table == "products":
        required_string("sku")
        required_string("product_name")
        decimal("unit_price")
        integer("batch_multiple", minimum=1)
        if type(payload.get("is_active")) is not bool:
            bad("is_active_invalid", "is_active must be boolean")
    elif table == "machines":
        required_string("machine_code")
        required_string("machine_name")
        enum("status", {"available", "maintenance", "retired"}, "machine_status_invalid", "machine status")
    elif table == "inventory":
        fk("product_id", "products")
        integer("available_quantity", minimum=0)
    elif table == "orders":
        fk("customer_id", "customers")
        enum("status", {"pending", "confirmed", "shipped"}, "order_status_invalid", "order status")
        decimal("total_amount")
    elif table == "order_items":
        fk("order_id", "orders")
        fk("product_id", "products")
        integer("quantity", minimum=1)
        decimal("unit_price")
    elif table == "production_orders":
        fk("product_id", "products")
        shortage = integer("shortage_quantity", minimum=1)
        batch = integer("batch_multiple", minimum=1)
        buffer_rate = parse_decimal(payload.get("buffer_rate"))
        if buffer_rate is None or buffer_rate < 0 or buffer_rate >= 1:
            bad("buffer_rate_invalid", "buffer_rate must be in [0, 1)")
        planned = integer("planned_quantity", minimum=1)
        if batch and planned and planned % batch:
            bad("planned_quantity_batch_mismatch", "planned_quantity must be a multiple of batch_multiple")
        enum("status", {"planned", "released", "completed"}, "production_order_status_invalid",
             "production order status")
        if not isinstance(payload.get("planned_start_date"), str):
            bad("planned_start_date_invalid", "planned_start_date must be an ISO date")
        else:
            try:
                date.fromisoformat(payload["planned_start_date"])
            except ValueError:
                bad("planned_start_date_invalid", "planned_start_date must be an ISO date")
    elif table == "production_runs":
        fk("production_order_id", "production_orders")
        fk("machine_id", "machines")
        start_time = timestamp("start_time")
        end_time = timestamp("end_time", nullable=True)
        actual = payload.get("actual_quantity")
        status = payload.get("status")
        enum("status", {"running", "completed", "failed"}, "production_run_status_invalid",
             "production run status")
        if actual is not None and (type(actual) is not int or actual < 0):
            bad("actual_quantity_invalid", "actual_quantity must be a nonnegative integer or null")
        if start_time and end_time and end_time < start_time:
            bad("production_run_time_invalid", "end_time cannot precede start_time")
        if status == "running" and (end_time is not None or actual is not None):
            bad("running_run_fields_invalid", "running runs require null end_time and actual_quantity")
        if status in {"completed", "failed"} and (end_time is None or actual is None):
            bad("finished_run_fields_invalid", "finished runs require end_time and actual_quantity")
    elif table == "maintenance_records":
        fk("machine_id", "machines")
        enum("maintenance_type", {"preventive", "corrective"}, "maintenance_type_invalid", "maintenance_type")
        status = payload.get("status")
        enum("status", {"scheduled", "in_progress", "completed"}, "maintenance_status_invalid",
             "maintenance status")
        start_time = timestamp("start_time", nullable=True)
        end_time = timestamp("end_time", nullable=True)
        cost = decimal("maintenance_cost", nullable=True)
        if start_time and end_time and end_time < start_time:
            bad("maintenance_time_invalid", "end_time cannot precede start_time")
        if status == "scheduled" and (start_time is not None or end_time is not None):
            bad("scheduled_maintenance_fields_invalid", "scheduled maintenance requires null start and end times")
        if status == "in_progress" and (start_time is None or end_time is not None):
            bad("active_maintenance_fields_invalid", "in-progress maintenance requires start and null end time")
        if status == "completed" and (start_time is None or end_time is None or cost is None):
            bad("completed_maintenance_fields_invalid", "completed maintenance requires start, end and cost")

    return findings


def validate_batch(connection, batch_id, now=lambda: datetime.now(timezone.utc)):
    """Validate one landed batch; persist quality results atomically and idempotently."""
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                        (f"zepic_quality:{batch_id}:{RULE_VERSION}",))
            cur.execute("SELECT source_table, row_count FROM zepic_raw.extraction_batches "
                        "WHERE batch_id=%s", (batch_id,))
            batch = cur.fetchone()
            if batch is None:
                raise ValueError(f"Raw batch {batch_id} does not exist")
            table, expected_count = batch
            cur.execute("SELECT input_rows, valid_rows, quarantined_rows, finding_count "
                        "FROM zepic_raw.quality_runs WHERE batch_id=%s AND rule_version=%s",
                        (batch_id, RULE_VERSION))
            existing = cur.fetchone()
            if existing is not None:
                return {"batch_id": batch_id, "table": table, "rule_version": RULE_VERSION,
                        "input_rows": existing[0], "valid_rows": existing[1],
                        "quarantined_rows": existing[2], "finding_count": existing[3], "skipped": True}
            cur.execute("SELECT source_table, source_primary_key, source_updated_at, payload "
                        "FROM zepic_raw.records WHERE batch_id=%s ORDER BY source_primary_key", (batch_id,))
            rows = cur.fetchall()
            if len(rows) != expected_count:
                raise RuntimeError(f"Raw batch {batch_id} metadata says {expected_count} rows but holds {len(rows)}")
            cur.execute("SELECT source_table, source_primary_key FROM zepic_raw.records")
            references = {}
            for source_table, source_primary_key in cur.fetchall():
                references.setdefault(source_table, set()).add(source_primary_key)

            started_at = now()
            all_findings = []
            quarantined_keys = set()
            for row_table, source_primary_key, source_updated_at, payload in rows:
                findings = validate_record(table, source_primary_key, source_updated_at, payload, references)
                if row_table != table:
                    findings.append(("source_table_mismatch",
                                     "record source_table must match its extraction batch source_table"))
                for rule_code, reason in findings:
                    all_findings.append((batch_id, row_table, source_primary_key, RULE_VERSION,
                                         rule_code, reason, started_at))
                    quarantined_keys.add(source_primary_key)
            input_rows = len(rows)
            quarantined_rows = len(quarantined_keys)
            cur.execute("INSERT INTO zepic_raw.quality_runs "
                        "(batch_id, rule_version, source_table, input_rows, valid_rows, quarantined_rows, "
                        "finding_count, started_at, completed_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (batch_id, RULE_VERSION, table, input_rows, input_rows - quarantined_rows,
                         quarantined_rows, len(all_findings), started_at, now()))
            if all_findings:
                cur.executemany("INSERT INTO zepic_raw.quality_findings "
                                "(batch_id, source_table, source_primary_key, rule_version, rule_code, reason, detected_at) "
                                "VALUES (%s,%s,%s,%s,%s,%s,%s)", all_findings)
    return {"batch_id": batch_id, "table": table, "rule_version": RULE_VERSION,
            "input_rows": input_rows, "valid_rows": input_rows - quarantined_rows,
            "quarantined_rows": quarantined_rows, "finding_count": len(all_findings), "skipped": False}


def validate_pending(connection, now=lambda: datetime.now(timezone.utc)):
    with connection.cursor() as cur:
        cur.execute("SELECT b.batch_id FROM zepic_raw.extraction_batches b "
                    "LEFT JOIN zepic_raw.quality_runs q ON q.batch_id=b.batch_id AND q.rule_version=%s "
                    "WHERE q.batch_id IS NULL ORDER BY b.batch_id", (RULE_VERSION,))
        batch_ids = [row[0] for row in cur.fetchall()]
    return [validate_batch(connection, batch_id, now=now) for batch_id in batch_ids]


def count_latest_quarantined_records(connection):
    """Count source keys whose latest quality-checked version is quarantined."""
    with connection.cursor() as cur:
        cur.execute(
            "WITH ranked AS ("
            "SELECT r.batch_id, r.source_table, r.source_primary_key, "
            "row_number() OVER (PARTITION BY r.source_table, r.source_primary_key "
            "ORDER BY r.source_updated_at DESC, r.batch_id DESC) AS version_rank "
            "FROM zepic_raw.records r "
            "WHERE EXISTS (SELECT 1 FROM zepic_raw.quality_runs q "
            "WHERE q.batch_id=r.batch_id AND q.rule_version=%s)"
            ") "
            "SELECT count(*) FROM ranked latest "
            "WHERE latest.version_rank=1 AND EXISTS ("
            "SELECT 1 FROM zepic_raw.quality_findings f "
            "WHERE f.batch_id=latest.batch_id "
            "AND f.source_table=latest.source_table "
            "AND f.source_primary_key=latest.source_primary_key "
            "AND f.rule_version=%s)",
            (RULE_VERSION, RULE_VERSION),
        )
        return cur.fetchone()[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-id", type=int, help="Validate one batch; defaults to all unvalidated batches")
    parser.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="Exit nonzero when any latest source record remains quarantined",
    )
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    required = ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")
    if any(not os.getenv(key) for key in required):
        parser.exit(2, "Set POSTGRES_DB, POSTGRES_USER and POSTGRES_PASSWORD in .env or environment.\n")
    import psycopg
    try:
        with psycopg.connect(dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"],
                             password=os.environ["POSTGRES_PASSWORD"],
                             host=os.getenv("POSTGRES_HOST", "localhost"),
                             port=os.getenv("POSTGRES_PORT", "5432"), connect_timeout=5,
                             autocommit=True) as conn:
            conn.execute((ROOT / "database/quality_schema.sql").read_text())
            results = ([validate_batch(conn, args.batch_id)] if args.batch_id is not None
                       else validate_pending(conn))
            latest_quarantined_records = count_latest_quarantined_records(conn)
    except Exception as exc:
        parser.exit(1, f"Raw validation failed ({type(exc).__name__}); no partial quality run was committed.\n")
    summary = {"rule_version": RULE_VERSION, "batches": len(results),
               "input_rows": sum(result["input_rows"] for result in results),
               "valid_rows": sum(result["valid_rows"] for result in results),
               "quarantined_rows": sum(result["quarantined_rows"] for result in results),
               "finding_count": sum(result["finding_count"] for result in results),
               "latest_quarantined_records": latest_quarantined_records,
               "results": results}
    print(json.dumps(summary, indent=2))
    if args.fail_on_findings and latest_quarantined_records:
        parser.exit(
            1,
            f"Quality gate failed: {latest_quarantined_records} latest source record(s) are quarantined.\n",
        )


if __name__ == "__main__":
    main()
