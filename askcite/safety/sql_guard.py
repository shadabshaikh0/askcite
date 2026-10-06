"""Check a SQL query before it is allowed to run.

Rules (all must pass):
1. Exactly one statement, and it must be a read-only SELECT (UNION / WITH allowed).
2. Nothing that writes or changes anything: no INSERT/UPDATE/DELETE/MERGE/DDL/COPY,
   no SELECT ... INTO, no FOR UPDATE locks, including inside CTEs and subqueries.
3. No dangerous functions (sleep, file access, dblink, query_to_xml, config changes, ...).
4. Only real tables from the schema, never system catalogs or blocked tables.
5. No personal or secret columns anywhere in the query, and no `*` / whole-row
   access (e.g. row_to_json(u)) on tables that have them.

The database user is also read-only, so even a mistake here cannot change data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from askcite.safety.pii import SensitiveColumns
from askcite.schema.catalog import Catalog

_BLOCKED_FUNCTIONS = {
    "pg_sleep", "pg_sleep_for", "pg_sleep_until", "pg_read_file", "pg_read_binary_file", "pg_ls_dir",
    "pg_stat_file", "pg_file_write", "pg_logdir_ls", "lo_import", "lo_export", "lo_get", "lo_put", "lo_from_bytea",
    "lo_open", "loread", "dblink", "dblink_exec", "dblink_connect", "dblink_open", "set_config",
    "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf", "pg_rotate_logfile", "pg_advisory_lock",
    "pg_advisory_xact_lock", "pg_try_advisory_lock", "query_to_xml", "query_to_xmlschema",
    "query_to_xml_and_xmlschema", "table_to_xml", "table_to_xmlschema", "cursor_to_xml", "database_to_xml",
    "schema_to_xml", "txid_current", "nextval", "setval", "currval", "pg_notify", "pg_create_restore_point",
    "pg_switch_wal", "pg_promote", "copy",
}
_WRITE_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.Command,
                exp.TruncateTable, exp.Copy, exp.Into, exp.Lock, exp.Grant, exp.Set, exp.Transaction,
                exp.Commit, exp.Rollback)
_SYSTEM_SCHEMAS = {"pg_catalog", "information_schema", "pg_toast"}
_WHOLE_ROW_FUNCTIONS = {"row_to_json", "to_json", "to_jsonb", "json_agg", "jsonb_agg", "array_agg", "hstore",
                        "json_build_array", "jsonb_build_array", "concat", "concat_ws", "format", "string_agg"}


@dataclass
class GuardResult:
    ok: bool
    sql: str
    errors: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    needs_approval: bool = False

    def explain(self) -> str:
        return "OK" if self.ok else "Blocked: " + "; ".join(self.errors)


class SqlGuard:
    def __init__(self, catalog: Catalog, sensitive: SensitiveColumns, allowed_schemas: list[str] | None = None,
                 blocked_tables: list[str] | None = None, approval_tables: list[str] | None = None):
        self.catalog = catalog
        self.sensitive = sensitive
        self.allowed_schemas = {s.lower() for s in (allowed_schemas or ["public"])}
        self.blocked_tables = {t.lower() for t in (blocked_tables or [])}
        self.approval_tables = {t.lower() for t in (approval_tables or [])}

    def check(self, sql: str) -> GuardResult:
        sql = (sql or "").strip().rstrip(";").strip()
        result = GuardResult(ok=False, sql=sql)
        if not sql:
            result.errors.append("empty query")
            return result
        try:
            statements = [s for s in sqlglot.parse(sql, dialect="postgres") if s is not None]
        except ParseError as error:
            result.errors.append(f"could not read the SQL ({str(error).splitlines()[0]})")
            return result
        if len(statements) != 1:
            result.errors.append("only one statement is allowed")
            return result
        tree = statements[0]
        if not isinstance(tree, (exp.Select, exp.SetOperation)):
            result.errors.append("only SELECT queries are allowed")
            return result

        for node in tree.walk():
            if isinstance(node, _WRITE_NODES):
                result.errors.append(f"not allowed: {node.key.upper()}")
            elif isinstance(node, exp.Func):
                name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
                if name in _BLOCKED_FUNCTIONS:
                    result.errors.append(f"function not allowed: {name}")
        if result.errors:
            return result

        cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
        alias_to_table: dict[str, str] = {}
        tables: list[str] = []
        for table in tree.find_all(exp.Table):
            name = table.name.lower()
            schema = (table.db or "").lower()
            if not name or (name in cte_names and not schema):
                continue
            if schema in _SYSTEM_SCHEMAS or name.startswith("pg_"):
                result.errors.append(f"system tables are not allowed: {table.sql(dialect='postgres')}")
                continue
            if schema and schema not in self.allowed_schemas:
                result.errors.append(f"schema not allowed: {schema}")
                continue
            if self.catalog.table(name) is None:
                result.errors.append(f"unknown table: {name}")
                continue
            if name in self.blocked_tables:
                result.errors.append(f"table not allowed: {name}")
                continue
            alias_to_table[(table.alias_or_name or name).lower()] = name
            alias_to_table.setdefault(name, name)
            if name not in tables:
                tables.append(name)
        result.tables = tables
        if result.errors:
            return result

        self._check_columns(tree, alias_to_table, tables, result)
        if result.errors:
            return result
        result.needs_approval = any(t in self.approval_tables for t in tables)
        result.ok = True
        return result

    def _check_columns(self, tree: exp.Expression, alias_to_table: dict[str, str], tables: list[str],
                       result: GuardResult) -> None:
        sensitive_by_table = {t: self.sensitive.for_table(t) for t in tables}
        for star in tree.find_all(exp.Star):
            parent = star.parent
            qualifier = parent.table.lower() if isinstance(parent, exp.Column) and parent.table else None
            in_count = isinstance(parent, exp.Count) or (
                isinstance(parent, exp.Column) and isinstance(parent.parent, exp.Count))
            if in_count:
                continue  # count(*) reads no column values
            targets = [alias_to_table.get(qualifier, qualifier)] if qualifier else tables
            for target in targets:
                hidden = sensitive_by_table.get(target or "", {})
                if hidden:
                    result.errors.append(
                        f"'*' is not allowed on {target} because it has personal/secret columns "
                        f"({', '.join(sorted(hidden))}); list the columns you need"
                    )
        for column in tree.find_all(exp.Column):
            name = column.name.lower()
            if not name:
                continue
            qualifier = column.table.lower() if column.table else None
            # A bare alias used as a value (e.g. row_to_json(u)) means "the whole row".
            if qualifier is None and name in alias_to_table and self._is_whole_row(column, alias_to_table[name]):
                target = alias_to_table[name]
                if sensitive_by_table.get(target):
                    result.errors.append(f"whole-row access to {target} is not allowed (it has personal columns)")
                continue
            candidates = [alias_to_table.get(qualifier, qualifier)] if qualifier else tables
            for target in candidates:
                kind = sensitive_by_table.get(target or "", {}).get(name)
                if kind:
                    result.errors.append(f"{target}.{name} is {kind} data and cannot be queried")
                    break

    def _is_whole_row(self, column: exp.Column, table: str) -> bool:
        table_def = self.catalog.table(table)
        if table_def is not None and table_def.column(column.name) is not None:
            return False  # a real column that happens to share the alias name
        parent = column.parent
        while parent is not None and not isinstance(parent, exp.Func):
            parent = parent.parent
        return parent is None or parent.sql_name().lower() in _WHOLE_ROW_FUNCTIONS or isinstance(parent, exp.Anonymous)
