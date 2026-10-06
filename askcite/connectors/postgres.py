from __future__ import annotations

import psycopg
from psycopg.conninfo import make_conninfo

from askcite.config import DatabaseSource, Settings, SourcesConfig
from askcite.connectors.base import Connector, Field, TestResult

_SAFETY = """
select
  (select rolsuper from pg_roles where rolname = current_user)                       as superuser,
  pg_is_in_recovery()                                                                  as replica,
  current_setting('default_transaction_read_only')                                     as default_read_only,
  count(*)                                                                             as tables,
  count(*) filter (where has_table_privilege(c.oid, 'INSERT') or has_table_privilege(c.oid, 'UPDATE')
                      or has_table_privilege(c.oid, 'DELETE') or has_table_privilege(c.oid, 'TRUNCATE')) as writable,
  count(*) filter (where pg_get_userbyid(c.relowner) = current_user)                   as owned
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where c.relkind in ('r', 'p') and n.nspname = any(%s)
"""


class PostgresConnector(Connector):
    kind = "postgres"
    title = "PostgreSQL (read-only)"
    description = ("Live numbers. Askcite reads table names, writes one SELECT per question and runs it "
                   "read-only. Rows are never stored.")
    fields = [
        Field("host", "Host", placeholder="replica.xxxx.ap-south-1.rds.amazonaws.com",
              help="Prefer a read replica."),
        Field("port", "Port", "number", default=5432),
        Field("database", "Database"),
        Field("user", "User", placeholder="askcite_ro", help="A read-only user (see README)."),
        Field("password", "Password", "password", secret=True),
        Field("sslmode", "SSL", "select", default="prefer",
              options=[("prefer", "prefer"), ("require", "require"), ("verify-full", "verify-full"),
                       ("disable", "disable")]),
        Field("url", "…or a full connection URL", "password", secret=True,
              placeholder="postgresql://user:password@host:5432/db", help="Used instead of the fields above."),
        Field("schemas", "Schemas", "list", default=["public"]),
        Field("max_rows", "Max rows per answer", "number", default=500),
        Field("timeout_seconds", "Query time limit (seconds)", "number", default=15),
        Field("approval_tables", "Tables that need an approver", "list"),
        Field("blocked_tables", "Tables never queried", "list"),
        Field("extra_sensitive_columns", "Extra personal columns", "list", placeholder="users.nickname",
              help="Hidden like phone/email/PAN, which are detected automatically."),
        Field("allow_writable", "Allow a user that can write (not recommended)", "checkbox",
              help="Askcite always runs queries in read-only transactions, but a read-only user is the "
                   "real safety net."),
    ]

    def default_name(self, config: dict) -> str:
        return config.get("database") or "db"

    @staticmethod
    def connection_url(config: dict, secrets: dict) -> str:
        if secrets.get("url"):
            return secrets["url"]
        return make_conninfo(host=config.get("host") or "localhost", port=config.get("port") or 5432,
                             dbname=config.get("database") or "", user=config.get("user") or "",
                             password=secrets.get("password") or "", sslmode=config.get("sslmode") or "prefer")

    def test(self, config: dict, secrets: dict, settings: Settings) -> TestResult:
        schemas = config.get("schemas") or ["public"]
        try:
            with psycopg.connect(self.connection_url(config, secrets), connect_timeout=8, autocommit=True) as conn:
                conn.read_only = True
                user, database = conn.execute("select current_user, current_database()").fetchone()
                (superuser, replica, default_ro, tables, writable,
                 owned) = conn.execute(_SAFETY, (schemas,)).fetchone()
        except psycopg.Error as error:
            return TestResult(False, f"Could not connect: {str(error).strip().splitlines()[0]}")
        details = [f"{tables} tables in {', '.join(schemas)}", f"user {user} on {database}"]
        if replica:
            details.append("This is a read replica, so writes are impossible")
            return TestResult(True, f"Connected — read-only replica ({tables} tables)", details=details)
        problems = []
        if superuser:
            problems.append("the user is a superuser")
        if owned:
            problems.append(f"the user owns {owned} table(s)")
        if writable:
            problems.append(f"the user can change data in {writable} table(s)")
        result = TestResult(True, f"Connected ({tables} tables)", details=details)
        if default_ro != "on":
            result.warnings.append("Tip: ALTER ROLE … SET default_transaction_read_only = on")
        if problems:
            text = "Not read-only: " + "; ".join(problems) + ". Use a read-only user (see README)."
            if config.get("allow_writable"):
                result.warnings.append(text + " Allowed because you ticked “allow anyway”.")
            else:
                result.ok, result.blocked, result.message = False, True, text
        return result

    def apply(self, sources: SourcesConfig, name: str, config: dict, secrets: dict) -> None:
        sources.database = DatabaseSource(
            name=name, url=self.connection_url(config, secrets), schema_from="live",
            schemas=config.get("schemas") or ["public"], max_rows=config.get("max_rows") or 500,
            timeout_seconds=config.get("timeout_seconds") or 15,
            approval_tables=config.get("approval_tables") or [], blocked_tables=config.get("blocked_tables") or [],
            extra_sensitive_columns=config.get("extra_sensitive_columns") or [], origin="connector",
        )

    def source_keys(self, name: str) -> list[str]:
        return [f"schema:{name}"]
