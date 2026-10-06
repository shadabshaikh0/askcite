from pathlib import Path

import pytest

from askcite.config import AccessConfig, Glossary, Settings, SourcesConfig
from askcite.safety.pii import SensitiveColumns
from askcite.safety.sql_guard import SqlGuard
from askcite.sources.schema_file import catalog_from_file

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def catalog():
    return catalog_from_file(FIXTURES / "demo_schema.sql", source="demo")


@pytest.fixture()
def guard(catalog):
    return SqlGuard(catalog, SensitiveColumns(catalog), approval_tables=["payments"])


@pytest.fixture()
def settings(tmp_path):
    return Settings(
        config_dir=tmp_path, data_dir=tmp_path / "data", store_url="postgresql://unused/unused",
        sources=SourcesConfig.model_validate({"timezone": "Asia/Kolkata", "database": {"name": "demo"},
                                               "code": [], "limits": {"max_tool_calls": 6, "max_queries": 2}}),
        access=AccessConfig(), glossary=Glossary.model_validate({"tables": {"orders": "customer orders"},
                                                                 "terms": {"settled": "money reached us"}}),
    )


STORE_ADMIN_URL = __import__("os").environ.get("ASKCITE_TEST_STORE_URL",
                                               "postgresql://askcite:askcite@localhost:5433/askcite")


def _postgres_or_skip():
    import psycopg

    try:
        psycopg.connect(STORE_ADMIN_URL, connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("local Postgres is not running (docker compose up -d postgres)")


@pytest.fixture(scope="session")
def test_store_url():
    """A fresh, separate database for tests, so tests never touch your real Askcite data."""
    import re

    import psycopg
    from psycopg import sql as pgsql

    from askcite.storage.db import init_db

    _postgres_or_skip()
    with psycopg.connect(STORE_ADMIN_URL, autocommit=True) as admin:
        admin.execute(pgsql.SQL("drop database if exists askcite_test_store with (force)"))
        admin.execute(pgsql.SQL("create database askcite_test_store"))
    url = re.sub(r"/[^/?]+(\?|$)", r"/askcite_test_store\1", STORE_ADMIN_URL, count=1)
    init_db(url)
    return url


@pytest.fixture(scope="session")
def fake_db_url():
    """A fake database (demo schema, made-up rows) with a read-only user."""
    from askcite.data.fakedb import build_fake_database

    _postgres_or_skip()
    report = build_fake_database(catalog_from_file(FIXTURES / "demo_schema.sql"), STORE_ADMIN_URL,
                                 database="askcite_test_fake", rows_per_table=50,
                                 readonly_role="askcite_test_ro", readonly_password="ro")
    assert report["tables"] == 3 and not report["skipped"]
    return report["readonly_url"]


@pytest.fixture()
def store(test_store_url):
    from askcite.storage.db import connect

    with connect(test_store_url) as conn:
        for table in ("connector", "sync_run", "doc_section", "docs_file_state", "app_setting", "code_chunk",
                      "sql_example", "repo_state", "schema_snapshot"):
            conn.execute(f"truncate {table} cascade")  # noqa: S608 - fixed table names
        yield conn
