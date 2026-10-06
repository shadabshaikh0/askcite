"""Run a checked SELECT on the read-only database.

- Read-only transaction, statement time limit, row limit (rows are streamed with a
  server-side cursor, so we stop after `max_rows`).
- Results live only in memory for the answer; they are never written anywhere.
- Every attempt is written to `query_audit` (SQL, tables, row count, timing — no rows).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg import sql as pgsql


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool
    duration_ms: int
    column_types: list[str] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def shape(self) -> dict:
        """What the cloud AI is allowed to know about a result: its shape, never its values."""
        return {
            "columns": self.columns,
            "column_types": self.column_types,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "single_value": self.row_count == 1 and len(self.columns) == 1,
        }


def _type_name(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, (float, Decimal)):
        return "number"
    if isinstance(value, datetime):
        return "timestamp"
    if isinstance(value, date):
        return "date"
    return "text"


class ReadOnlyRunner:
    def __init__(self, url: str, max_rows: int = 500, timeout_seconds: int = 15, schemas: list[str] | None = None):
        if not url:
            raise ValueError("The read-only database URL is not set (see READONLY_DB_URL in .env)")
        self.url = url
        self.max_rows = max_rows
        self.timeout_seconds = timeout_seconds
        self.schemas = schemas or ["public"]

    def run(self, checked_sql: str) -> QueryResult:
        started = time.monotonic()
        with psycopg.connect(self.url, autocommit=False) as conn:
            conn.read_only = True
            with conn.transaction(force_rollback=True):  # read-only anyway; never keep anything
                conn.execute(pgsql.SQL("set local statement_timeout = {}").format(
                    pgsql.Literal(f"{self.timeout_seconds}s")))
                conn.execute(pgsql.SQL("set local search_path = {}").format(
                    pgsql.SQL(", ").join(pgsql.Identifier(s) for s in self.schemas)))
                with conn.cursor(name="askcite_query") as cur:
                    cur.itersize = min(self.max_rows + 1, 1000)
                    cur.execute(checked_sql)
                    rows = cur.fetchmany(self.max_rows + 1)
                    columns = [d.name for d in cur.description or []]
        truncated = len(rows) > self.max_rows
        rows = [tuple(r) for r in rows[: self.max_rows]]
        types = [_type_name(next((r[i] for r in rows if r[i] is not None), None)) for i in range(len(columns))]
        return QueryResult(columns=columns, rows=rows, truncated=truncated,
                           duration_ms=int((time.monotonic() - started) * 1000), column_types=types)


def audit(store: psycopg.Connection | None, *, sql_text: str, tables: list[str], status: str,
          question: str | None = None, slack_user: str | None = None, question_id: int | None = None,
          row_count: int | None = None, duration_ms: int | None = None, error: str | None = None) -> int | None:
    """Write one audit row. The rows themselves are never stored."""
    if store is None:
        return None
    row = store.execute(
        "insert into query_audit (question_id, slack_user, question, sql_text, tables, status, row_count, "
        "duration_ms, error) values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
        (question_id, slack_user, question, sql_text, tables, status, row_count, duration_ms, error),
    ).fetchone()
    return row["id"] if isinstance(row, dict) else row[0]
