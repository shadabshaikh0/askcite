"""Saved connectors and sync history, in Askcite's own database."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import psycopg

from askcite.secrets import SecretBox


@dataclass
class ConnectorRecord:
    id: int
    kind: str
    name: str
    config: dict
    secrets_encrypted: str | None
    enabled: bool
    updated_at: datetime


def _record(row: dict) -> ConnectorRecord:
    return ConnectorRecord(row["id"], row["kind"], row["name"], row["config"] or {}, row["secrets"], row["enabled"],
                           row["updated_at"])


def list_connectors(store: psycopg.Connection) -> list[ConnectorRecord]:
    return [_record(r) for r in store.execute("select * from connector order by kind, name").fetchall()]


def get_connector(store: psycopg.Connection, key: int | str) -> ConnectorRecord | None:
    column = "id" if isinstance(key, int) else "name"
    row = store.execute(f"select * from connector where {column} = %s", (key,)).fetchone()  # noqa: S608
    return _record(row) if row else None


def save_connector(store: psycopg.Connection, box: SecretBox, kind: str, name: str, config: dict, secrets: dict,
                   connector_id: int | None = None) -> int:
    encrypted = box.encrypt(secrets) if secrets else None
    if connector_id is None:
        row = store.execute("insert into connector (kind, name, config, secrets) values (%s, %s, %s, %s) "
                            "returning id", (kind, name, json.dumps(config), encrypted)).fetchone()
        return row["id"]
    store.execute("update connector set name = %s, config = %s, secrets = %s, updated_at = now() where id = %s",
                  (name, json.dumps(config), encrypted, connector_id))
    return connector_id


def delete_connector(store: psycopg.Connection, connector_id: int) -> None:
    store.execute("delete from connector where id = %s", (connector_id,))


def set_enabled(store: psycopg.Connection, connector_id: int, enabled: bool) -> None:
    store.execute("update connector set enabled = %s, updated_at = now() where id = %s", (enabled, connector_id))


# ---- sync history ------------------------------------------------------------------------------

def start_sync(store: psycopg.Connection, source: str) -> int:
    return store.execute("insert into sync_run (source, status) values (%s, 'running') returning id",
                         (source,)).fetchone()["id"]


def finish_sync(store: psycopg.Connection, run_id: int, status: str, stats: dict | None = None,
                error: str | None = None) -> None:
    store.execute("update sync_run set finished_at = now(), status = %s, stats = %s, error = %s where id = %s",
                  (status, json.dumps(stats or {}, default=str), error, run_id))


def latest_syncs(store: psycopg.Connection) -> dict[str, dict]:
    """Most recent sync per source, plus the last successful one."""
    rows = store.execute("""
        select distinct on (source) source, status, started_at, finished_at, stats, error
        from sync_run order by source, started_at desc
    """).fetchall()
    last_ok = {r["source"]: r for r in store.execute("""
        select distinct on (source) source, finished_at, stats from sync_run where status = 'ok'
        order by source, started_at desc
    """).fetchall()}
    return {r["source"]: {**r, "last_ok": last_ok.get(r["source"])} for r in rows}
