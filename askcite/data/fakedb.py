"""Build a local FAKE database from a schema, for testing without real data.

The fake database has the same tables and columns as your real one, filled with
made-up rows (deterministic, so tests are repeatable). Foreign-key columns point
at existing fake parent rows, and CHECK (col IN (...)) lists are respected, so
joins and status filters return sensible results.
"""

from __future__ import annotations

import random
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
from psycopg import sql as pgsql

from askcite.schema.catalog import Catalog, Table


def _safe_type(declared: str) -> str:
    t = (declared or "text").lower().strip()
    if t.endswith("[]") or t.startswith("array"):
        return "text[]"
    if re.match(r"^(big|small)?serial|^(big|small)?int|^integer", t):
        return "bigint"
    if re.match(r"^(numeric|decimal|real|double|float|money)", t):
        return "numeric"
    if t.startswith("bool"):
        return "boolean"
    if t.startswith("timestamptz") or "with time zone" in t:
        return "timestamptz"
    if t.startswith("timestamp") or t == "datetime":
        return "timestamp"
    if t == "date":
        return "date"
    if t.startswith("time"):
        return "time"
    if t.startswith("json"):
        return "jsonb"
    if t == "uuid":
        return "uuid"
    if t.startswith("bytea"):
        return "bytea"
    return "text"


def _order_tables(catalog: Catalog) -> list[Table]:
    """Parents before children, so child foreign keys can point at existing parent keys."""
    tables = {name: t for name, t in catalog.tables.items() if not t.is_view and t.columns}
    ordered, seen = [], set()

    def visit(name: str, stack: set[str]) -> None:
        if name in seen or name in stack or name not in tables:
            return
        stack.add(name)
        for fk in tables[name].foreign_keys:
            visit(fk.ref_table.lower(), stack)
        stack.discard(name)
        seen.add(name)
        ordered.append(tables[name])

    for name in sorted(tables):
        visit(name, set())
    return ordered


class FakeRows:
    def __init__(self, catalog: Catalog, rows_per_table: int = 200, seed: int = 7, days: int = 60):
        self.catalog = catalog
        self.rows_per_table = rows_per_table
        self.random = random.Random(seed)
        self.now = datetime.now(UTC).replace(microsecond=0)
        self.days = days
        self.keys: dict[tuple[str, str], list] = {}  # (table, column) -> values, for foreign keys
        self.one_each: dict[tuple[str, str], list] = {}  # shuffled parent keys for one-row-per-parent tables

    def value(self, table: Table, column_name: str, column_type: str, allowed: list[str] | None, row: int):
        fk = next((f for f in table.foreign_keys if column_name in f.columns), None)
        if fk is not None:
            ref_column = fk.ref_columns[fk.columns.index(column_name)] if len(fk.ref_columns) > fk.columns.index(
                column_name) else column_name
            parents = self.keys.get((fk.ref_table.lower(), ref_column.lower()))
            if parents and table.primary_key == [column_name]:  # one row per parent, e.g. a status per user
                key = (table.name, column_name)
                if key not in self.one_each:
                    self.one_each[key] = self.random.sample(parents, len(parents))
                order = self.one_each[key]
                if row < len(order):
                    return order[row]
            if parents:
                return self.random.choice(parents)
        if allowed:
            return self.random.choice(allowed)
        is_key = column_name in table.primary_key or column_name == "id"
        if column_type == "bigint":
            return row + 1 if is_key else self.random.randint(1, self.rows_per_table)
        if column_type == "numeric":
            return Decimal(self.random.randint(100, 10_000_000)) / Decimal(100)
        if column_type == "boolean":
            return self.random.random() < 0.5
        if column_type in ("timestamp", "timestamptz"):
            moment = self.now - timedelta(seconds=self.random.randint(0, self.days * 86400))
            return moment if column_type == "timestamptz" else moment.replace(tzinfo=None)
        if column_type == "date":
            return (self.now - timedelta(days=self.random.randint(0, self.days))).date()
        if column_type == "time":
            return (self.now - timedelta(seconds=self.random.randint(0, 86400))).time()
        if column_type == "jsonb":
            return "{}"
        if column_type == "uuid":
            return uuid.UUID(int=self.random.getrandbits(128))
        if column_type == "text[]":
            return [f"{column_name}_{self.random.randint(1, 5)}"]
        if column_type == "bytea":
            return b""
        if is_key:
            return f"{column_name}_{row + 1}"
        return f"{column_name}_{self.random.randint(1, 20)}"


def _add_constraints(conn: psycopg.Connection, catalog: Catalog) -> None:
    """Best effort: copy CHECK lists, primary keys and foreign keys, so that reading the fake database's
    schema live gives the same allowed values and links as the original. Anything that does not fit the
    fake rows (e.g. a key made of foreign-key columns with repeated values) is simply skipped."""

    def attempt(statement) -> None:
        try:
            conn.execute(statement)
        except psycopg.Error:
            pass

    tables = {name: t for name, t in catalog.tables.items() if not t.is_view and t.columns}
    for table in tables.values():
        for column in table.columns:
            if column.allowed_values:
                attempt(pgsql.SQL("alter table {} add check ({} in ({}))").format(
                    pgsql.Identifier(table.name), pgsql.Identifier(column.name),
                    pgsql.SQL(", ").join(pgsql.Literal(v) for v in column.allowed_values)))
        if table.primary_key:
            attempt(pgsql.SQL("alter table {} add primary key ({})").format(
                pgsql.Identifier(table.name), pgsql.SQL(", ").join(pgsql.Identifier(c) for c in table.primary_key)))
    for table in tables.values():
        for fk in table.foreign_keys:
            if fk.ref_table.lower() in tables and len(fk.columns) == len(fk.ref_columns):
                attempt(pgsql.SQL("alter table {} add foreign key ({}) references {} ({})").format(
                    pgsql.Identifier(table.name), pgsql.SQL(", ").join(pgsql.Identifier(c) for c in fk.columns),
                    pgsql.Identifier(fk.ref_table), pgsql.SQL(", ").join(pgsql.Identifier(c) for c in fk.ref_columns)))


def build_fake_database(catalog: Catalog, admin_url: str, database: str = "askcite_fake",
                        rows_per_table: int = 200, readonly_role: str = "askcite_fake_ro",
                        readonly_password: str = "askcite_fake_ro") -> dict:
    """(Re)create `database` with fake rows and a read-only role. Returns a small report."""
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(pgsql.SQL("drop database if exists {} with (force)").format(pgsql.Identifier(database)))
        admin.execute(pgsql.SQL("create database {}").format(pgsql.Identifier(database)))
        exists = admin.execute("select 1 from pg_roles where rolname = %s", (readonly_role,)).fetchone()
        verb = "alter" if exists else "create"  # always (re)set the password, so the printed URL works
        admin.execute(pgsql.SQL(verb + " role {} login password {}").format(
            pgsql.Identifier(readonly_role), pgsql.Literal(readonly_password)))

    target_url = re.sub(r"/[^/?]+(\?|$)", f"/{database}\\1", admin_url, count=1)
    faker = FakeRows(catalog, rows_per_table=rows_per_table)
    report = {"tables": 0, "rows": 0, "skipped": []}
    with psycopg.connect(target_url, autocommit=True) as conn:
        for table in _order_tables(catalog):
            columns = [(c.name, _safe_type(c.type), c.allowed_values) for c in table.columns]
            ddl = pgsql.SQL("create table {} ({})").format(
                pgsql.Identifier(table.name),
                pgsql.SQL(", ").join(pgsql.SQL("{} {}").format(pgsql.Identifier(n), pgsql.SQL(t))
                                     for n, t, _ in columns),
            )
            try:
                conn.execute(ddl)
            except psycopg.Error as error:
                report["skipped"].append(f"{table.name}: {error}".splitlines()[0])
                continue
            rows = [[faker.value(table, n, t, allowed, i) for n, t, allowed in columns]
                    for i in range(rows_per_table)]
            copy_sql = pgsql.SQL("copy {} ({}) from stdin").format(
                pgsql.Identifier(table.name), pgsql.SQL(", ").join(pgsql.Identifier(n) for n, _, _ in columns))
            with conn.cursor() as cur, cur.copy(copy_sql) as copy:
                for row in rows:
                    copy.write_row(row)
            for index, (name, _, _) in enumerate(columns):
                faker.keys[(table.name.lower(), name.lower())] = [r[index] for r in rows]
            report["tables"] += 1
            report["rows"] += len(rows)
        _add_constraints(conn, catalog)
        conn.execute(pgsql.SQL("grant connect on database {} to {}").format(
            pgsql.Identifier(database), pgsql.Identifier(readonly_role)))
        conn.execute(pgsql.SQL("grant usage on schema public to {}").format(pgsql.Identifier(readonly_role)))
        conn.execute(pgsql.SQL("grant select on all tables in schema public to {}").format(
            pgsql.Identifier(readonly_role)))
        conn.execute(pgsql.SQL("alter role {} set default_transaction_read_only = on").format(
            pgsql.Identifier(readonly_role)))
        conn.execute("analyze")
    host_part = re.sub(r"^postgres(ql)?://[^@]*@", "", target_url)
    report["readonly_url"] = f"postgresql://{readonly_role}:{readonly_password}@{host_part}"
    return report
