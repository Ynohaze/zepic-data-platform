"""Extract source API pages and atomically land each table batch plus watermark."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from psycopg.types.json import Jsonb


ROOT = Path(__file__).resolve().parents[2]
TABLES = {
    "customers": "customer_id",
    "products": "product_id",
    "machines": "machine_id",
    "inventory": "product_id",
    "orders": "order_id",
    "order_items": "order_item_id",
    "production_orders": "production_order_id",
    "production_runs": "production_run_id",
    "maintenance_records": "maintenance_record_id",
}
RAW_SCHEMA_LOCK_KEY = "zepic_raw:schema"


def ensure_raw_schema(connection, schema_sql):
    """Serialize idempotent Raw DDL across parallel extraction processes."""
    connection.execute(
        "SELECT pg_advisory_lock(hashtext(%s))", (RAW_SCHEMA_LOCK_KEY,)
    )
    try:
        connection.execute(schema_sql)
    finally:
        connection.execute(
            "SELECT pg_advisory_unlock(hashtext(%s))", (RAW_SCHEMA_LOCK_KEY,)
        )


def parse_timestamp(value):
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("API timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def request_json(url, attempts=3, timeout=10, opener=urlopen, sleeper=time.sleep):
    """Retry transient HTTP/network failures with bounded exponential backoff."""
    for attempt in range(attempts):
        try:
            request = Request(url, headers={"Accept": "application/json"})
            with opener(request, timeout=timeout) as response:
                return json.loads(response.read())
        except HTTPError as exc:
            exc.close()
            if exc.code < 500 and exc.code != 429:
                raise
            error = exc
        except (URLError, TimeoutError, OSError) as exc:
            error = exc
        if attempt + 1 == attempts:
            raise error
        sleeper(0.2 * (2 ** attempt))


def fetch_batch(table, base_url, after, page_size=500, request=request_json):
    """Fetch all pages under one fixed source upper bound."""
    if table not in TABLES:
        raise ValueError(f"Unsupported source table: {table}")
    base_url = base_url.rstrip("/")
    bounds = request(f"{base_url}/v1/{table}/bounds")
    upper = bounds["upper_bound"]
    if upper is None:
        return None, []
    cursor = after
    data = []
    while True:
        params = {"upper_updated_at": upper["updated_at"], "upper_id": upper["primary_key"],
                  "limit": page_size}
        if cursor is not None:
            params["after_updated_at"] = cursor["updated_at"]
            params["after_id"] = cursor["primary_key"]
        page = request(f"{base_url}/v1/{table}?{urlencode(params)}")
        data.extend(page["data"])
        if not page["has_more"]:
            break
        next_cursor = page["next_cursor"]
        if next_cursor is None or next_cursor == cursor:
            raise RuntimeError(f"Source API did not advance the cursor for {table}")
        cursor = next_cursor
    return upper, data


def extract_table(connection, table, base_url, page_size=500, request=request_json,
                  now=lambda: datetime.now(timezone.utc)):
    """Persist one full table batch and advance its watermark in one transaction."""
    if table not in TABLES:
        raise ValueError(f"Unsupported source table: {table}")
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute("SELECT last_updated_at, last_primary_key, initialized "
                        "FROM zepic_raw.watermarks WHERE source_table = %s", (table,))
            watermark = cur.fetchone()
    after = None if watermark is None or watermark[0] is None else {
        "updated_at": watermark[0].astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "primary_key": watermark[1],
    }
    initialized = watermark is not None and watermark[2]
    started_at = now()
    upper, records = fetch_batch(table, base_url, after, page_size=page_size, request=request)
    upper_at = parse_timestamp(upper["updated_at"]) if upper else None
    upper_id = upper["primary_key"] if upper else None
    lower_at = parse_timestamp(after["updated_at"]) if after else None
    lower_id = after["primary_key"] if after else None

    # Record raw source states as returned. Re-reading an unchanged upper bound is
    # an empty completed batch and leaves the watermark untouched.
    advance = upper is not None and (after is None or
              (upper_at, upper_id) > (lower_at, lower_id))
    if not initialized and upper is None:
        advance = False

    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("zepic_raw:" + table,))
            cur.execute("SELECT last_updated_at, last_primary_key, initialized "
                        "FROM zepic_raw.watermarks WHERE source_table = %s FOR UPDATE", (table,))
            current = cur.fetchone()
            if current != watermark:
                raise RuntimeError(f"Watermark for {table} changed during extraction; rerun the batch")
            cur.execute("INSERT INTO zepic_raw.extraction_batches "
                        "(source_table, lower_updated_at, lower_primary_key, upper_updated_at, "
                        "upper_primary_key, row_count, started_at, completed_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING batch_id",
                        (table, lower_at, lower_id, upper_at, upper_id, len(records), started_at, started_at))
            batch_id = cur.fetchone()[0]
            for row in records:
                primary_key = row[TABLES[table]]
                cur.execute("INSERT INTO zepic_raw.records "
                            "(batch_id, source_table, source_primary_key, source_updated_at, payload, extracted_at) "
                            "VALUES (%s,%s,%s,%s,%s,%s)",
                            (batch_id, table, primary_key, parse_timestamp(row["updated_at"]),
                             Jsonb(row), started_at))
            cur.execute("UPDATE zepic_raw.extraction_batches SET completed_at=%s WHERE batch_id=%s",
                        (now(), batch_id))
            if watermark is None:
                cur.execute("INSERT INTO zepic_raw.watermarks "
                            "(source_table, last_updated_at, last_primary_key, initialized) "
                            "VALUES (%s,%s,%s,TRUE)",
                            (table, upper_at if advance else None, upper_id if advance else None))
            elif advance:
                cur.execute("UPDATE zepic_raw.watermarks "
                            "SET last_updated_at=%s, last_primary_key=%s, initialized=TRUE, updated_at=%s "
                            "WHERE source_table=%s", (upper_at, upper_id, now(), table))
            elif not initialized:
                cur.execute("UPDATE zepic_raw.watermarks SET initialized=TRUE, updated_at=%s "
                            "WHERE source_table=%s", (now(), table))
    return {"table": table, "batch_id": batch_id, "rows": len(records),
            "watermark": {"updated_at": upper_at.isoformat().replace("+00:00", "Z"),
                          "primary_key": upper_id} if advance else after,
            "initialized": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--table", choices=[*TABLES, "all"], default="all")
    parser.add_argument("--page-size", type=int, default=500)
    schema_mode = parser.add_mutually_exclusive_group()
    schema_mode.add_argument("--init-only", action="store_true",
                             help="initialize or upgrade Raw schema, then exit")
    schema_mode.add_argument("--skip-schema-init", action="store_true",
                             help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.page_size <= 1000:
        parser.error("--page-size must be between 1 and 1000")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    required = ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")
    if any(not os.getenv(key) for key in required):
        parser.exit(2, "Set POSTGRES_DB, POSTGRES_USER and POSTGRES_PASSWORD in .env or environment.\n")
    import psycopg
    tables = TABLES if args.table == "all" else {args.table: TABLES[args.table]}
    try:
        with psycopg.connect(dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"],
                             password=os.environ["POSTGRES_PASSWORD"],
                             host=os.getenv("POSTGRES_HOST", "localhost"),
                             port=os.getenv("POSTGRES_PORT", "5432"), connect_timeout=5,
                             autocommit=True) as conn:
            conn.execute("SET search_path TO public")
            if not args.skip_schema_init:
                ensure_raw_schema(conn, (ROOT / "database/raw_schema.sql").read_text())
            if args.init_only:
                print(json.dumps({"raw_schema_initialized": True}))
                return
            results = [extract_table(conn, table, args.base_url, args.page_size) for table in tables]
    except Exception as exc:
        parser.exit(1, f"Extraction failed ({type(exc).__name__}); the current table batch was rolled back.\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
