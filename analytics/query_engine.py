"""Read-only execution of SQL accepted by the analytical query guard."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any, Callable

from analytics.query_guard import QueryGuard


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    source_models: tuple[str, ...]
    executed_sql: str
    elapsed_ms: int
    truncated: bool


class QueryEngine:
    """Validate and execute one bounded query in a read-only transaction."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        guard: QueryGuard | None = None,
        statement_timeout_ms: int = 5_000,
        max_rows: int = 200,
    ):
        if not 1 <= statement_timeout_ms <= 30_000:
            raise ValueError("statement_timeout_ms must be between 1 and 30000")
        if not 1 <= max_rows <= 1_000:
            raise ValueError("max_rows must be between 1 and 1000")

        self.connection_factory = connection_factory
        self.guard = guard or QueryGuard()
        self.statement_timeout_ms = statement_timeout_ms
        self.max_rows = max_rows

    def execute(self, proposed_sql: str) -> QueryResult:
        validated = self.guard.validate(proposed_sql)
        bounded_sql = (
            "SELECT * FROM (\n"
            f"{validated.sql}\n"
            f") AS zepic_bounded_result LIMIT {self.max_rows + 1}"
        )
        started = monotonic()

        with self.connection_factory() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
                    cursor.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        (str(self.statement_timeout_ms),),
                    )
                    cursor.execute(bounded_sql)
                    fetched = cursor.fetchall()
                    columns = tuple(column.name for column in cursor.description)

        truncated = len(fetched) > self.max_rows
        rows = tuple(tuple(row) for row in fetched[: self.max_rows])
        elapsed_ms = round((monotonic() - started) * 1_000)
        return QueryResult(
            columns=columns,
            rows=rows,
            source_models=validated.source_models,
            executed_sql=validated.sql,
            elapsed_ms=elapsed_ms,
            truncated=truncated,
        )
