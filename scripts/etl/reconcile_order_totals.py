"""Reconcile current landed order totals against their current detail rows."""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.etl.validate_raw import RULE_VERSION as VALIDATION_RULE_VERSION

RULE_VERSION = "order_totals_v1"


def _latest_records(connection, table):
    with connection.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (r.source_primary_key) r.batch_id, b.source_table, r.source_table, "
            "r.source_primary_key, r.source_updated_at, r.payload "
            "FROM zepic_raw.records r "
            "JOIN zepic_raw.extraction_batches b USING (batch_id) "
            "WHERE b.source_table=%s "
            "ORDER BY r.source_primary_key, r.source_updated_at DESC, r.batch_id DESC",
            (table,),
        )
        return cur.fetchall()


def _snapshot_hash(orders, items, invalid_keys):
    digest = hashlib.sha256()
    for table, rows in (("orders", orders), ("order_items", items)):
        for batch_id, batch_table, record_table, primary_key, updated_at, payload in rows:
            canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
            encoded = json.dumps((table, batch_id, batch_table, record_table, primary_key,
                                  updated_at.isoformat(), canonical_payload), separators=(",", ":"))
            digest.update(encoded.encode("utf-8"))
            digest.update(b"\n")
    digest.update(json.dumps(sorted(invalid_keys), separators=(",", ":")).encode("utf-8"))
    return digest.hexdigest()


def reconcile_order_totals(connection, now=lambda: datetime.now(timezone.utc)):
    """Compare latest valid order headers/items and persist one atomic snapshot run."""
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                        (f"zepic_order_reconciliation:{RULE_VERSION}:{VALIDATION_RULE_VERSION}",))
            cur.execute(
                "SELECT b.batch_id FROM zepic_raw.extraction_batches b "
                "LEFT JOIN zepic_raw.quality_runs q ON q.batch_id=b.batch_id AND q.rule_version=%s "
                "WHERE q.batch_id IS NULL ORDER BY b.batch_id LIMIT 10",
                (VALIDATION_RULE_VERSION,),
            )
            pending = [row[0] for row in cur.fetchall()]
            if pending:
                raise RuntimeError(
                    f"Run row-level validation ({VALIDATION_RULE_VERSION}) first; "
                    f"unvalidated batches include {pending}"
                )

            orders = _latest_records(connection, "orders")
            items = _latest_records(connection, "order_items")
            cur.execute("SELECT batch_id, source_primary_key FROM zepic_raw.quality_findings "
                        "WHERE rule_version=%s", (VALIDATION_RULE_VERSION,))
            invalid_keys = {(row[0], row[1]) for row in cur.fetchall()}
            snapshot_hash = _snapshot_hash(orders, items, invalid_keys)

            cur.execute(
                "SELECT run_id, input_orders, checked_orders, matched_orders, mismatched_orders, "
                "missing_item_orders, skipped_orders FROM zepic_raw.order_reconciliation_runs "
                "WHERE rule_version=%s AND validation_rule_version=%s AND snapshot_hash=%s",
                (RULE_VERSION, VALIDATION_RULE_VERSION, snapshot_hash),
            )
            existing = cur.fetchone()
            if existing:
                return {"run_id": existing[0], "input_orders": existing[1],
                        "checked_orders": existing[2], "matched_orders": existing[3],
                        "mismatched_orders": existing[4], "missing_item_orders": existing[5],
                        "skipped_orders": existing[6], "skipped": True}

            items_by_order = {}
            invalid_items_by_order = set()
            for batch_id, batch_table, source_table, item_id, updated_at, payload in items:
                order_id = payload.get("order_id") if isinstance(payload, dict) else None
                if type(order_id) is not int:
                    continue
                if ((batch_id, item_id) in invalid_keys or batch_table != "order_items"
                        or source_table != "order_items"):
                    invalid_items_by_order.add(order_id)
                    continue
                items_by_order.setdefault(order_id, []).append(payload)

            started_at = now()
            findings = []
            checked_orders = matched_orders = mismatched_orders = missing_item_orders = skipped_orders = 0
            for batch_id, batch_table, source_table, order_id, updated_at, payload in orders:
                if (batch_table != "orders" or source_table != "orders"
                        or (batch_id, order_id) in invalid_keys):
                    skipped_orders += 1
                    continue
                detail_rows = items_by_order.get(order_id, [])
                if order_id in invalid_items_by_order:
                    skipped_orders += 1
                    continue

                header_total = Decimal(payload["total_amount"])
                items_total = sum(
                    (Decimal(item["unit_price"]) * item["quantity"] for item in detail_rows),
                    Decimal("0.00"),
                )
                detail_count = len(detail_rows)
                checked_orders += 1
                if detail_count == 0:
                    missing_item_orders += 1
                    findings.append((batch_id, order_id, "order_items_missing", header_total,
                                     items_total, detail_count,
                                     "order has no valid current order_items rows"))
                elif header_total != items_total:
                    mismatched_orders += 1
                    findings.append((batch_id, order_id, "order_total_mismatch", header_total,
                                     items_total, detail_count,
                                     f"order total {header_total} does not equal item total {items_total}"))
                else:
                    matched_orders += 1

            completed_at = now()
            input_orders = len(orders)
            cur.execute(
                "INSERT INTO zepic_raw.order_reconciliation_runs "
                "(rule_version, validation_rule_version, snapshot_hash, input_orders, checked_orders, "
                "matched_orders, mismatched_orders, missing_item_orders, skipped_orders, started_at, completed_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING run_id",
                (RULE_VERSION, VALIDATION_RULE_VERSION, snapshot_hash, input_orders, checked_orders,
                 matched_orders, mismatched_orders, missing_item_orders, skipped_orders, started_at, completed_at),
            )
            run_id = cur.fetchone()[0]
            if findings:
                cur.executemany(
                    "INSERT INTO zepic_raw.order_reconciliation_findings "
                    "(run_id, batch_id, order_id, rule_code, order_total, items_total, detail_count, reason) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    [(run_id, *finding) for finding in findings],
                )

    return {"run_id": run_id, "input_orders": input_orders, "checked_orders": checked_orders,
            "matched_orders": matched_orders, "mismatched_orders": mismatched_orders,
            "missing_item_orders": missing_item_orders, "skipped_orders": skipped_orders,
            "skipped": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="Exit nonzero when orders mismatch, lack details, or are skipped",
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
            result = reconcile_order_totals(conn)
    except Exception as exc:
        parser.exit(1, f"Order reconciliation failed ({type(exc).__name__}); no partial result was committed.\n")
    print(json.dumps(result, sort_keys=True))
    issue_count = result["mismatched_orders"] + result["missing_item_orders"] + result["skipped_orders"]
    if args.fail_on_findings and issue_count:
        parser.exit(
            1,
            "Order reconciliation gate failed: "
            f"{result['mismatched_orders']} mismatch(es), "
            f"{result['missing_item_orders']} missing-detail order(s), and "
            f"{result['skipped_orders']} skipped order(s).\n",
        )


if __name__ == "__main__":
    main()
