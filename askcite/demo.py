"""`askcite demo setup`: a working Askcite in one command, with the fake demo shop.

- Uses its own storage database (askcite_demo) and data folder, so it never mixes with your setup.
- Turns examples/demo-shop/app into a git repository and builds a FAKE shop database from schema.sql.
- Adds three connectors (code repo, docs folder, read-only database) and runs the first sync.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
from pathlib import Path

import psycopg
from psycopg import sql as pgsql

DEMO_DB = "askcite_demo"
CONNECTORS = ("demo-shop-app", "demo-shop-docs", "demo-shop-db")


def _with_database(url: str, database: str) -> str:
    return re.sub(r"/[^/?]+(\?|$)", f"/{database}\\1", url, count=1)


def _git_repo_from(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    env = {**os.environ, "GIT_AUTHOR_NAME": "Demo", "GIT_AUTHOR_EMAIL": "demo@example.com",
           "GIT_COMMITTER_NAME": "Demo", "GIT_COMMITTER_EMAIL": "demo@example.com",
           "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"}
    for args in (["init", "-q", "-b", "main"], ["add", "."], ["commit", "-q", "-m", "Demo shop"]):
        subprocess.run(["git", *args], cwd=target, env=env, check=True, capture_output=True)


def demo_is_ready(store_url: str) -> bool:
    """True when all three demo connectors exist and their last sync succeeded."""
    try:
        with psycopg.connect(store_url, connect_timeout=5) as conn:
            names = {row[0] for row in conn.execute("select name from connector where name = any(%s)",
                                                     (list(CONNECTORS),))}
            failed = conn.execute("select count(*) from (select distinct on (source) status from sync_run "
                                  "order by source, started_at desc) s where status <> 'ok'").fetchone()[0]
        return names == set(CONNECTORS) and failed == 0
    except psycopg.Error:
        return False


def setup_demo(example_dir: Path, base_store_url: str, rows: int = 300, docs_web_url: str | None = None,
               echo=print, in_place: bool = False, data_dir: Path | None = None) -> dict:
    from askcite import config
    from askcite.connectors.store import delete_connector, get_connector, save_connector
    from askcite.data.fakedb import build_fake_database
    from askcite.indexing import forget_source
    from askcite.runtime import Runtime
    from askcite.sources.schema_file import catalog_from_file
    from askcite.storage.db import init_db

    example_dir = example_dir.resolve()
    config_dir = example_dir / "config"
    echo("1/5 Preparing the demo storage database…")
    if in_place:  # a container already points ASKCITE_STORE_URL / ASKCITE_DATA_DIR at its own database and volume
        store_url, data_dir = base_store_url, Path(data_dir or "data").resolve()
        init_db(store_url)
        settings = config.get_settings()
    else:  # a laptop checkout: keep the demo in its own database, remembered in config/.env
        data_dir = (example_dir.parent.parent / "data" / "demo").resolve()
        store_url = _with_database(base_store_url, DEMO_DB)
        with psycopg.connect(base_store_url, autocommit=True) as admin:
            if not admin.execute("select 1 from pg_database where datname = %s", (DEMO_DB,)).fetchone():
                admin.execute(pgsql.SQL("create database {}").format(pgsql.Identifier(DEMO_DB)))
        init_db(store_url)
        (config_dir / ".env").write_text(
            "# Written by `askcite demo setup` — keeps the demo separate from your own setup.\n"
            f"ASKCITE_STORE_URL={store_url}\nASKCITE_DATA_DIR={data_dir}\n")
        os.environ.update({"ASKCITE_CONFIG_DIR": str(config_dir), "ASKCITE_STORE_URL": store_url,
                           "ASKCITE_DATA_DIR": str(data_dir)})
        config.get_settings.cache_clear()
        settings = config.get_settings()

    echo("2/5 Turning examples/demo-shop/app into a git repository…")
    repo_dir = data_dir / "demo-shop-app"
    _git_repo_from(example_dir / "app", repo_dir)

    echo(f"3/5 Building the FAKE shop database ({rows} made-up rows per table)…")
    catalog = catalog_from_file(example_dir / "schema.sql", source="demo-shop-db")
    password = secrets.token_urlsafe(16)
    fake = build_fake_database(catalog, base_store_url, database="askcite_demo_shop", rows_per_table=rows,
                               readonly_role="askcite_demo_ro", readonly_password=password)

    echo("4/5 Adding connectors: code repo, docs folder, read-only database…")
    runtime = Runtime(settings)
    for name in CONNECTORS:
        record = get_connector(runtime.store, name)
        if record is not None:
            forget_source(runtime.store, record.kind, record.name)
            delete_connector(runtime.store, record.id)
    box, store = runtime.box, runtime.store
    save_connector(store, box, "git", "demo-shop-app",
                   {"url": f"file://{repo_dir}", "auth": "none", "include": ["**/*.kt"], "exclude": []}, {})
    save_connector(store, box, "folder", "demo-shop-docs",
                   {"path": str(example_dir / "docs"), "include": ["**/*.md"], "web_url": docs_web_url}, {})
    save_connector(store, box, "postgres", "demo-shop-db",
                   {"schemas": ["public"], "max_rows": 500, "timeout_seconds": 15, "approval_tables": [],
                    "blocked_tables": [], "extra_sensitive_columns": []}, {"url": fake["readonly_url"]})

    echo("5/5 First sync…")
    runtime.reload_sources()
    results = runtime.sync()
    return {"store_url": store_url, "config_dir": config_dir, "sync": results, "tables": fake["tables"],
            "rows": fake["rows"]}
