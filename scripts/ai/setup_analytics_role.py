"""Create or update the least-privilege PostgreSQL role used by analytics."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql


ROOT = Path(__file__).resolve().parents[2]
ANALYTICS_SCHEMA = "zepic_analytics"


def provision_analytics_role(connection, role_name: str, password: str) -> None:
    if not role_name or not password:
        raise ValueError("The analytics role name and password are required.")

    role_exists = connection.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)",
        (role_name,),
    ).fetchone()[0]
    role = sql.Identifier(role_name)

    if role_exists:
        connection.execute(
            sql.SQL(
                "ALTER ROLE {} WITH LOGIN PASSWORD {} NOSUPERUSER "
                "NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
            ).format(role, sql.Literal(password)),
        )
    else:
        connection.execute(
            sql.SQL(
                "CREATE ROLE {} WITH LOGIN PASSWORD {} NOSUPERUSER "
                "NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
            ).format(role, sql.Literal(password)),
        )

    database_name = connection.info.dbname
    connection.execute(
        sql.SQL("REVOKE ALL PRIVILEGES ON DATABASE {} FROM {}").format(
            sql.Identifier(database_name), role
        )
    )
    user_schemas = [
        row[0]
        for row in connection.execute(
            "SELECT nspname FROM pg_namespace "
            "WHERE nspname <> 'information_schema' AND nspname NOT LIKE 'pg_%'"
        ).fetchall()
    ]
    for schema_name in user_schemas:
        schema = sql.Identifier(schema_name)
        connection.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {} FROM {}").format(
                schema, role
            )
        )
        connection.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA {} FROM {}").format(
                schema, role
            )
        )
        connection.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA {} FROM {}").format(
                schema, role
            )
        )
        connection.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA {} "
                "REVOKE ALL PRIVILEGES ON TABLES FROM {}"
            ).format(schema, role)
        )

    connection.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(database_name), role
        )
    )
    connection.execute(
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
            sql.Identifier(ANALYTICS_SCHEMA), role
        )
    )
    connection.execute(
        sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(
            sql.Identifier(ANALYTICS_SCHEMA), role
        )
    )
    connection.execute(
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA {} "
            "GRANT SELECT ON TABLES TO {}"
        ).format(sql.Identifier(ANALYTICS_SCHEMA), role)
    )
    connection.execute(
        sql.SQL("ALTER ROLE {} SET default_transaction_read_only TO on").format(
            role
        )
    )
    connection.execute(
        sql.SQL("ALTER ROLE {} SET statement_timeout TO '5s'").format(role)
    )
    connection.execute(
        sql.SQL(
            "ALTER ROLE {} SET search_path TO {}, pg_catalog"
        ).format(role, sql.Identifier(ANALYTICS_SCHEMA))
    )


def main() -> None:
    load_dotenv(ROOT / ".env", override=False)
    required = (
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "ANALYTICS_DB_USER",
        "ANALYTICS_DB_PASSWORD",
    )
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise SystemExit("Missing required settings: " + ", ".join(missing))

    with psycopg.connect(
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        connect_timeout=5,
        autocommit=True,
    ) as connection:
        provision_analytics_role(
            connection,
            os.environ["ANALYTICS_DB_USER"],
            os.environ["ANALYTICS_DB_PASSWORD"],
        )

    print(
        "Analytics role configured with read-only access to "
        f"{ANALYTICS_SCHEMA}."
    )


if __name__ == "__main__":
    main()
