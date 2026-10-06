import logging

from askcite import indexing
from askcite.config import CodeSource, NotionSource
from askcite.runtime import refresh_once
from askcite.tools import Workspace
from tests.conftest import FIXTURES


class FakeStore:
    """Just enough of a psycopg connection for index_schema: no snapshot yet, inserts are recorded."""

    def __init__(self):
        self.inserted = []

    def execute(self, sql, params=None):
        if sql.lstrip().lower().startswith("insert"):
            self.inserted.append(params)
        return self

    def fetchone(self):
        return None


def test_missing_notion_token_does_not_block_code_or_schema(settings, catalog, monkeypatch, caplog):
    settings.sources.code = [CodeSource(name="demo", url="file:///tmp/demo")]
    settings.sources.notion = NotionSource(token_env="ASKCITE_TEST_NOTION_TOKEN_UNSET")
    settings.sources.database.schema_file = str(FIXTURES / "demo_schema.sql")
    calls = []

    def failing_code_refresh(s, store):
        calls.append("code")
        raise RuntimeError("git is down")

    monkeypatch.setattr(indexing, "index_code", lambda s, store, **kw: failing_code_refresh(s, store))
    monkeypatch.setattr(indexing, "index_notion", lambda s, store, **kw: calls.append("notion"))
    monkeypatch.setattr(indexing, "index_schema", lambda s, store, **kw: calls.append("schema"))
    monkeypatch.setattr(indexing, "load_catalog", lambda store, source: catalog)
    workspace = Workspace(settings, store=None, catalog=None, runner=None)
    skipped_logged = set()

    with caplog.at_level(logging.INFO, logger="askcite"):
        first = refresh_once(settings, workspace, None, settings.sources, skipped_logged)
        second = refresh_once(settings, workspace, None, settings.sources, skipped_logged)

    assert first == {"git:demo": "failed", "notion": "skipped (not configured)", "schema:demo": "ok"}
    assert "notion" not in calls and calls.count("schema") == 2  # a failing code refresh didn't stop the schema
    assert workspace.guard is not None and workspace.catalog is catalog
    assert second == first
    assert sum("notion is not configured" in r.message for r in caplog.records) == 1  # noqa: E501  # said once, not every round


def test_schema_from_file_ignores_the_database_url(settings, monkeypatch):
    monkeypatch.setenv("READONLY_DB_URL", "postgresql://fake-only/db")
    settings.sources.database.schema_file = str(FIXTURES / "demo_schema.sql")
    settings.sources.database.schema_from = "file"
    store = FakeStore()
    report = indexing.index_schema(settings, store)
    assert report["origin"] == "file" and report["tables"] == 3 and report["changed"]
    assert store.inserted[0][1] == "file"
