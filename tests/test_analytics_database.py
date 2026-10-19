"""Opt in with ZEPIC_DB_TESTS=1; uses a temporary database and login role."""

import os
from pathlib import Path
import unittest
import uuid

from analytics.query_engine import QueryEngine
from scripts.ai.setup_analytics_role import provision_analytics_role


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    os.getenv("ZEPIC_DB_TESTS") == "1",
    "Set ZEPIC_DB_TESTS=1 for isolated PostgreSQL tests",
)
class AnalyticsDatabaseTests(unittest.TestCase):
    def test_role_and_engine_enforce_gold_only_read_access(self):
        import psycopg
        from dotenv import load_dotenv
        from psycopg import sql

        load_dotenv(ROOT / ".env", override=False)
        admin_params = {
            "host": os.getenv("POSTGRES_HOST", "localhost"),
            "port": os.getenv("POSTGRES_PORT", "5432"),
            "user": os.environ["POSTGRES_USER"],
            "password": os.environ["POSTGRES_PASSWORD"],
            "autocommit": True,
        }
        suffix = uuid.uuid4().hex[:12]
        database_name = f"zepic_analytics_test_{suffix}"
        role_name = f"zepic_analytics_reader_{suffix}"
        role_password = uuid.uuid4().hex
        admin = None
        database_admin = None
        reader = None
        database_created = False
        role_created = False

        try:
            admin = psycopg.connect(
                **admin_params,
                dbname=os.environ["POSTGRES_DB"],
            )
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
            )
            database_created = True
            database_admin = psycopg.connect(
                **admin_params,
                dbname=database_name,
            )
            database_admin.execute("CREATE SCHEMA zepic_analytics")
            database_admin.execute("CREATE SCHEMA zepic_raw")
            database_admin.execute(
                "CREATE TABLE zepic_analytics.fct_orders "
                "(order_id bigint PRIMARY KEY, order_total_amount numeric(12, 2))"
            )
            database_admin.execute(
                "INSERT INTO zepic_analytics.fct_orders VALUES "
                "(1, 10.00), (2, 20.00), (3, 30.00)"
            )
            database_admin.execute(
                "CREATE TABLE zepic_raw.records (record_id bigint PRIMARY KEY)"
            )
            database_admin.execute("INSERT INTO zepic_raw.records VALUES (99)")

            provision_analytics_role(database_admin, role_name, role_password)
            role_created = True
            role_flags = database_admin.execute(
                "SELECT rolsuper, rolinherit, rolcreaterole, rolcreatedb, "
                "rolcanlogin, rolreplication, rolbypassrls "
                "FROM pg_roles WHERE rolname = %s",
                (role_name,),
            ).fetchone()
            self.assertEqual(
                role_flags,
                (False, False, False, False, True, False, False),
            )
            database_admin.execute(
                "CREATE TABLE zepic_analytics.later_model (value integer)"
            )
            database_admin.execute(
                "INSERT INTO zepic_analytics.later_model VALUES (1)"
            )

            reader_params = {
                "host": admin_params["host"],
                "port": admin_params["port"],
                "dbname": database_name,
                "user": role_name,
                "password": role_password,
            }
            engine = QueryEngine(
                lambda: psycopg.connect(**reader_params),
                max_rows=2,
            )
            result = engine.execute(
                "SELECT order_id FROM zepic_analytics.fct_orders ORDER BY order_id"
            )
            self.assertEqual(result.rows, ((1,), (2,)))
            self.assertTrue(result.truncated)

            reader = psycopg.connect(**reader_params)
            self.assertEqual(
                reader.execute(
                    "SELECT value FROM zepic_analytics.later_model"
                ).fetchone()[0],
                1,
            )
            reader.rollback()

            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                reader.execute(
                    "INSERT INTO zepic_analytics.fct_orders VALUES (4, 40.00)"
                )
            reader.rollback()

            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                reader.execute("SELECT * FROM zepic_raw.records")
            reader.rollback()
        finally:
            if reader is not None:
                reader.close()
            if database_admin is not None:
                database_admin.close()
            if admin is not None:
                if database_created:
                    admin.execute(
                        sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                            sql.Identifier(database_name)
                        )
                    )
                if role_created:
                    admin.execute(
                        sql.SQL("DROP ROLE IF EXISTS {}").format(
                            sql.Identifier(role_name)
                        )
                    )
                admin.close()


if __name__ == "__main__":
    unittest.main()
