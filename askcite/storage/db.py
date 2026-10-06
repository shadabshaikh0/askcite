"""Connection to Askcite's own storage database and simple migrations."""

from __future__ import annotations

from importlib import resources

import psycopg
from psycopg.rows import dict_row

from askcite.config import get_settings


def connect(url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(url or get_settings().store_url, row_factory=dict_row, autocommit=True)


def init_db(url: str | None = None) -> list[str]:
    """Apply migrations that have not run yet. Returns the names of the ones applied."""
    applied: list[str] = []
    with connect(url) as conn:
        conn.execute("create table if not exists schema_migrations (name text primary key, "
                     "applied_at timestamptz not null default now())")
        done = {row["name"] for row in conn.execute("select name from schema_migrations")}
        folder = resources.files("askcite.storage").joinpath("migrations")
        for entry in sorted(folder.iterdir(), key=lambda item: item.name):
            if not entry.name.endswith(".sql") or entry.name in done:
                continue
            with conn.transaction():
                conn.execute(entry.read_text())
                conn.execute("insert into schema_migrations (name) values (%s)", (entry.name,))
            applied.append(entry.name)
    return applied
