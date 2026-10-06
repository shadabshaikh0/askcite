"""Web page, CLI connectors and docs-folder indexing, against a separate test database."""

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from askcite.config import Settings
from askcite.indexing import index_docs_folders
from askcite.runtime import Runtime
from askcite.web.app import create_app
from askcite.web.auth import csrf_token

pytestmark = pytest.mark.integration
AUTH = ("admin", "test-password-123")


@pytest.fixture()
def runtime(settings: Settings, store, test_store_url, tmp_path, monkeypatch):
    monkeypatch.setenv("ASKCITE_ADMIN_PASSWORD", AUTH[1])
    settings = settings.model_copy(update={"data_dir": tmp_path / "data", "config_dir": tmp_path,
                                           "store_url": test_store_url})
    settings.sources.database = None
    return Runtime(settings, store=store)


@pytest.fixture()
def client(runtime):
    return TestClient(create_app(runtime))


@pytest.fixture()
def docs(tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "refunds.md").write_text("# Refund policy\nRefunds take 5 days.\n\n## Failed payments\nRetry twice.\n")
    return folder


def test_login_is_required(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/connectors").status_code == 401
    assert client.get("/connectors", auth=("admin", "wrong")).status_code == 401
    assert client.get("/connectors", auth=AUTH).status_code == 200


def test_posts_need_csrf_and_same_origin(client, runtime, docs):
    form = {"kind": "folder", "_name": "docs", "path": str(docs), "include": "**/*.md"}
    assert client.post("/connectors/save", data=form, auth=AUTH).status_code == 403
    token = csrf_token(runtime.box.derive(b"csrf"))
    evil = client.post("/connectors/save", data={**form, "csrf": token}, auth=AUTH,
                       headers={"origin": "https://evil.example"})
    assert evil.status_code == 403
    ok = client.post("/connectors/save", data={**form, "csrf": token}, auth=AUTH, follow_redirects=False)
    assert ok.status_code == 303 and "saved=docs" in ok.headers["location"]
    page = client.get("/connectors", auth=AUTH).text
    assert "docs" in page and "connector" in page


def test_test_button_and_secrets_never_shown(client, runtime, docs, monkeypatch):
    import httpx

    from askcite.connectors.notion import NotionConnector

    monkeypatch.setattr(NotionConnector, "transport", httpx.MockTransport(
        lambda request: httpx.Response(200, json={"name": "Askcite"} if request.url.path.endswith("/me")
                                       else {"results": [], "has_more": False})))
    token = csrf_token(runtime.box.derive(b"csrf"))
    tested = client.post("/connectors/test", data={"csrf": token, "kind": "folder", "path": str(docs)}, auth=AUTH)
    assert tested.json()["ok"] and tested.json()["message"] == "Found 1 document(s)"
    client.post("/connectors/save", data={"csrf": token, "kind": "notion", "token": "ntn_very_secret_value"},
                auth=AUTH)
    record = runtime.store.execute("select id, secrets from connector where kind = 'notion'").fetchone()
    assert "ntn_very_secret_value" not in record["secrets"]
    edit_page = client.get(f"/connectors/{record['id']}/edit", auth=AUTH).text
    assert "ntn_very_secret_value" not in edit_page and "saved — leave empty to keep" in edit_page
    # saving again with the secret left empty keeps it
    client.post("/connectors/save", data={"csrf": token, "kind": "notion", "id": record["id"], "token": ""},
                auth=AUTH)
    assert runtime.reload_sources().notion.resolve_token() == "ntn_very_secret_value"


def test_remove_deletes_connector_and_its_index(client, runtime, docs):
    token = csrf_token(runtime.box.derive(b"csrf"))
    client.post("/connectors/save", data={"csrf": token, "kind": "folder", "_name": "docs", "path": str(docs)},
                auth=AUTH)
    runtime.sync()
    assert runtime.store.execute("select count(*) n from doc_section").fetchone()["n"] == 2
    connector_id = runtime.store.execute("select id from connector where name = 'docs'").fetchone()["id"]
    client.post(f"/connectors/{connector_id}/delete", data={"csrf": token}, auth=AUTH)
    assert runtime.store.execute("select count(*) n from doc_section").fetchone()["n"] == 0
    assert runtime.store.execute("select count(*) n from connector").fetchone()["n"] == 0


def test_docs_folder_indexing_is_incremental(runtime, docs):
    from askcite.config import DocsFolderSource

    runtime.sources.docs_folders = [DocsFolderSource(name="docs", path=str(docs), web_url="https://x/y")]
    settings, store, sources = runtime.settings, runtime.store, runtime.sources
    assert index_docs_folders(settings, store, sources=sources)[0]["files_indexed"] == 1
    assert index_docs_folders(settings, store, sources=sources)[0]["files_indexed"] == 0  # unchanged: skipped
    (docs / "refunds.md").write_text("# Refund policy\nRefunds take 3 days now.\n")
    assert index_docs_folders(settings, store, sources=sources)[0]["files_indexed"] == 1
    row = store.execute("select url, content from doc_section where page_id = 'folder:docs/refunds.md'").fetchone()
    assert row["url"] == "https://x/y/refunds.md#refund-policy" and "3 days" in row["content"]
    (docs / "refunds.md").unlink()
    assert index_docs_folders(settings, store, sources=sources)[0]["files_removed"] == 1


def test_cli_connect_list_and_disconnect(test_store_url, tmp_path, docs, monkeypatch):
    from askcite import config
    from askcite.cli import app

    monkeypatch.setenv("ASKCITE_STORE_URL", test_store_url)
    monkeypatch.setenv("ASKCITE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("ASKCITE_DATA_DIR", str(tmp_path / "data"))
    config.get_settings.cache_clear()
    try:
        runner = CliRunner()
        result = runner.invoke(app, ["connect", "folder", "--name", "handbook", "--set", f"path={docs}", "--yes"])
        assert result.exit_code == 0, result.output
        assert "✓ Found 1 document(s)" in result.output and "Saved “handbook”" in result.output
        listing = runner.invoke(app, ["connectors"])
        assert "handbook" in listing.output and "ok" in listing.output
        missing = runner.invoke(app, ["connect", "folder", "--name", "x", "--set", "path=/nope", "--yes"])
        assert missing.exit_code == 1 and "Folder not found" in missing.output
        assert runner.invoke(app, ["disconnect", "handbook", "--yes"]).exit_code == 0
    finally:
        config.get_settings.cache_clear()
