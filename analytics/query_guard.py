"""Deterministic validation for LLM-proposed analytical SQL."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sqlglot import exp, parse
from sqlglot.errors import ParseError


DEFAULT_CONTEXT_PATH = Path(__file__).with_name("semantic_context.json")

# Deliberately small for Level 1. Additions should be driven by an accepted
# analytical question and covered by a validator test.
ALLOWED_FUNCTIONS = {
    "ABS",
    "AVG",
    "CASE",
    "CAST",
    "COALESCE",
    "COUNT",
    "DENSE_RANK",
    "EXTRACT",
    "GREATEST",
    "LAG",
    "LEAD",
    "LEAST",
    "LOWER",
    "MAX",
    "MIN",
    "NULLIF",
    "NTILE",
    "RANK",
    "ROUND",
    "ROW_NUMBER",
    "SUM",
    "TIMESTAMP_TRUNC",
    "UPPER",
}

PROHIBITED_EXPRESSION_TYPES = (
    exp.Alter,
    exp.Command,
    exp.Copy,
    exp.Create,
    exp.Delete,
    exp.Drop,
    exp.Grant,
    exp.Insert,
    exp.Into,
    exp.Lock,
    exp.Merge,
    exp.Revoke,
    exp.Set,
    exp.Transaction,
    exp.Update,
    exp.Use,
)


class QueryRejected(ValueError):
    """Raised when proposed SQL falls outside the Level 1 query contract."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ValidatedQuery:
    sql: str
    source_models: tuple[str, ...]


class QueryGuard:
    """Validate one read-only query against the declared Gold surface."""

    def __init__(self, context_path: Path = DEFAULT_CONTEXT_PATH):
        self.context = json.loads(context_path.read_text())
        self.allowed_schema = self.context["allowed_schema"]
        self.allowed_tables = frozenset(self.context["tables"])

    def validate(self, sql: str) -> ValidatedQuery:
        if not sql or not sql.strip():
            raise QueryRejected("empty_query", "The SQL proposal is empty.")

        try:
            statements = parse(sql, read=self.context["dialect"])
        except ParseError as exc:
            raise QueryRejected("invalid_sql", "The SQL proposal cannot be parsed.") from exc

        if len(statements) != 1:
            raise QueryRejected(
                "multiple_statements",
                "Exactly one SQL statement is allowed.",
            )

        statement = statements[0]
        if not isinstance(statement, exp.Query):
            raise QueryRejected("not_select", "Only a SELECT query is allowed.")

        prohibited = next(
            (
                node
                for node in statement.walk()
                if isinstance(node, PROHIBITED_EXPRESSION_TYPES)
            ),
            None,
        )
        if prohibited is not None:
            raise QueryRejected(
                "prohibited_operation",
                f"{type(prohibited).__name__.upper()} is not allowed.",
            )

        cte_names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
        source_models: set[str] = set()

        for table in statement.find_all(exp.Table):
            table_name = table.name.lower()
            schema_name = table.db.lower()
            catalog_name = table.catalog.lower()

            if not schema_name and not catalog_name and table_name in cte_names:
                continue
            if catalog_name:
                raise QueryRejected(
                    "catalog_access",
                    "Catalog-qualified relations are not allowed.",
                )
            if schema_name != self.allowed_schema:
                raise QueryRejected(
                    "schema_access",
                    f"Relations must be explicitly qualified with {self.allowed_schema}.",
                )
            if table_name not in self.allowed_tables:
                raise QueryRejected(
                    "table_access",
                    f"Relation {self.allowed_schema}.{table_name} is not approved.",
                )
            source_models.add(table_name)

        if not source_models:
            raise QueryRejected(
                "missing_gold_source",
                "The query must read at least one approved Gold model.",
            )

        for function in statement.find_all(exp.Func):
            if (
                isinstance(function, exp.If)
                and function.find_ancestor(exp.Case) is not None
            ):
                continue
            function_name = (
                function.name.upper()
                if isinstance(function, exp.Anonymous)
                else function.sql_name().upper()
            )
            if function_name not in ALLOWED_FUNCTIONS:
                raise QueryRejected(
                    "function_access",
                    f"Function {function_name} is not approved for analytical queries.",
                )

        return ValidatedQuery(
            sql=statement.sql(dialect=self.context["dialect"]),
            source_models=tuple(sorted(source_models)),
        )
