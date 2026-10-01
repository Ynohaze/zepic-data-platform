"""Read-only, keyset-paginated API over the operational PostgreSQL database."""

from datetime import datetime, timezone
from decimal import Decimal
import os
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=False)

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
MAX_PAGE_SIZE = 1000


def database_connection():
    """Open a short-lived connection; credentials come from environment/.env."""
    return psycopg.connect(
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        connect_timeout=5,
    )


def json_value(value: Any):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return value


def key_tuple(row, primary_key):
    return {"updated_at": json_value(row["updated_at"]), "primary_key": row[primary_key]}


def read_upper_bound(table: str):
    """Return this table's current maximum (updated_at, primary key), or None."""
    primary_key = TABLES[table][0]
    with database_connection() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            row = conn.execute(
                sql.SQL("SELECT updated_at, {} FROM public.{} "
                        "ORDER BY updated_at DESC, {} DESC LIMIT 1").format(
                            sql.Identifier(primary_key), sql.Identifier(table),
                            sql.Identifier(primary_key))).fetchone()
            if row is None:
                return None
            return {"updated_at": json_value(row[0]), "primary_key": row[1]}


def read_page(table: str, after_updated_at: datetime | None, after_id: int | None,
              upper_updated_at: datetime, upper_id: int, limit: int):
    primary_key = TABLES[table][0]
    where = sql.SQL("(updated_at, {}) <= (%s, %s)").format(sql.Identifier(primary_key))
    params: list[Any] = [upper_updated_at, upper_id]
    if after_updated_at is not None:
        where += sql.SQL(" AND (updated_at, {}) > (%s, %s)").format(sql.Identifier(primary_key))
        params.extend((after_updated_at, after_id))
    params.append(limit)
    query = sql.SQL("SELECT * FROM public.{} WHERE {} ORDER BY updated_at, {} LIMIT %s").format(
        sql.Identifier(table), where, sql.Identifier(primary_key))
    with database_connection() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            with conn.cursor() as cur:
                cur.execute(query, params)
                names = [column.name for column in cur.description]
                records = [{name: json_value(value) for name, value in zip(names, row)}
                           for row in cur.fetchall()]
    return records


app = FastAPI(title="Zepic Source API", version="1.0.0")


@app.get("/health")
def health():
    try:
        with database_connection() as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute("SELECT 1")
        return {"status": "ok"}
    except (KeyError, psycopg.Error):
        raise HTTPException(status_code=503, detail="Source database unavailable")


@app.get("/v1/{table}/bounds")
def bounds(table: str):
    if table not in TABLES:
        raise HTTPException(status_code=404, detail="Table is not enabled for extraction")
    try:
        return {"table": table, "upper_bound": read_upper_bound(table)}
    except (KeyError, psycopg.Error):
        raise HTTPException(status_code=503, detail="Source database unavailable")


@app.get("/v1/{table}")
def rows(
    table: str,
    upper_updated_at: datetime,
    upper_id: int,
    after_updated_at: datetime | None = None,
    after_id: int | None = None,
    limit: int = Query(default=500, ge=1, le=MAX_PAGE_SIZE),
):
    if table not in TABLES:
        raise HTTPException(status_code=404, detail="Table is not enabled for extraction")
    if (after_updated_at is None) != (after_id is None):
        raise HTTPException(status_code=422, detail="after_updated_at and after_id must be provided together")
    if upper_updated_at.tzinfo is None or upper_updated_at.utcoffset() is None:
        raise HTTPException(status_code=422, detail="upper_updated_at must include a timezone")
    if after_updated_at is not None and (after_updated_at.tzinfo is None or after_updated_at.utcoffset() is None):
        raise HTTPException(status_code=422, detail="after_updated_at must include a timezone")
    if after_updated_at is not None and (after_updated_at, after_id) > (upper_updated_at, upper_id):
        raise HTTPException(status_code=422, detail="after cursor exceeds the batch upper bound")
    try:
        data = read_page(table, after_updated_at, after_id, upper_updated_at, upper_id, limit)
        return {"table": table, "data": data,
                "next_cursor": key_tuple(data[-1], TABLES[table][0]) if data else None,
                "upper_bound": {"updated_at": json_value(upper_updated_at), "primary_key": upper_id},
                "has_more": len(data) == limit}
    except (KeyError, psycopg.Error):
        raise HTTPException(status_code=503, detail="Source database unavailable")
