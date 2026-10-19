"""Database connection settings for the governed analytics service."""

from __future__ import annotations

import os

import psycopg


def connect_analytics_database():
    required = ("POSTGRES_DB", "ANALYTICS_DB_USER", "ANALYTICS_DB_PASSWORD")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            "Missing analytics database settings: " + ", ".join(missing)
        )

    return psycopg.connect(
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["ANALYTICS_DB_USER"],
        password=os.environ["ANALYTICS_DB_PASSWORD"],
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        connect_timeout=5,
    )
