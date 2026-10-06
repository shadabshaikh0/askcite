import os
import subprocess

import httpx
import pytest
from cryptography.fernet import Fernet
from slack_sdk.errors import SlackApiError

from askcite.connectors import REGISTRY, effective_sources
from askcite.connectors import slack as slack_module
from askcite.connectors.notion import NotionConnector
from askcite.connectors.store import get_connector, save_connector
from askcite.secrets import SecretBox, SecretsUnavailable, load_or_create_key

# ---- secrets ------------------------------------------------------------------------------------


def test_secret_box_round_trip_and_private_key_file(tmp_path, monkeypatch):
    monkeypatch.delenv("ASKCITE_SECRET_KEY", raising=False)
    box = SecretBox.for_data_dir(tmp_path)
    token = box.encrypt({"password": "hunter2"})
    assert "hunter2" not in token and box.decrypt(token) == {"password": "hunter2"}
    assert oct(os.stat(tmp_path / "secret.key").st_mode)[-3:] == "600"
    assert load_or_create_key(tmp_path) == (tmp_path / "secret.key").read_bytes()  # reused, not regenerated
    with pytest.raises(SecretsUnavailable):  # a different key cannot read it
        SecretBox(Fernet.generate_key()).decrypt(token)


# ---- form parsing --------------------------------------------------------------------------------


def test_parse_keeps_saved_secret_when_left_blank_and_types_values():
    git = REGISTRY["git"]
    config, secrets = git.parse({"url": "git@x:a/b.git", "auth": "https_token", "token": "",
                                 "include": "src/**/*.kt,\n lib/**/*.java"}, previous_secrets={"token": "old"})
    assert secrets == {"token": "old"} and config["include"] == ["src/**/*.kt", "lib/**/*.java"]
    assert git.default_name(config) == "b"
    postgres = REGISTRY["postgres"]
    config, _ = postgres.parse({"port": "", "allow_writable": "on"})
    assert config["port"] == 5432 and config["allow_writable"] is True


def test_missing_respects_show_if():
    git = REGISTRY["git"]
    assert git.missing({"url": "x", "auth": "none"}, {}) == []
    assert git.missing({"url": "x", "auth": "ssh_key"}, {}) == ["SSH deploy key (private key)"]


# ---- connection tests -----------------------------------------------------------------------------


class FakeWebClient:
    def __init__(self, token):
        self.token = token

    def auth_test(self):
        if self.token != "xoxb-good":
            raise SlackApiError("bad", {"ok": False, "error": "invalid_auth"})
        return {"team": "Demo Team", "user": "askcite"}

    def apps_connections_open(self):
        if self.token != "xapp-good":
            raise SlackApiError("bad", {"ok": False, "error": "not_allowed_token_type"})
        return {"url": "wss://example"}


def test_slack_test(settings, monkeypatch):
    monkeypatch.setattr(slack_module, "WebClient", FakeWebClient)
    slack = REGISTRY["slack"]
    good = slack.test({}, {"bot_token": "xoxb-good", "app_token": "xapp-good"}, settings)
    assert good.ok and "Demo Team" in good.message
    bad_bot = slack.test({}, {"bot_token": "xoxb-nope", "app_token": "xapp-good"}, settings)
    assert not bad_bot.ok and "invalid_auth" in bad_bot.message
    swapped = slack.test({}, {"bot_token": "xoxb-good", "app_token": "xoxb-good"}, settings)
    assert not swapped.ok and "App-level token" in swapped.message


def test_notion_test(settings, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["authorization"] != "Bearer good":
            return httpx.Response(401, json={"message": "unauthorized"})
        if request.url.path == "/v1/users/me":
            return httpx.Response(200, json={"name": "Askcite"})
        return httpx.Response(200, json={"has_more": False, "results": [{
            "id": "p1", "url": "https://notion.so/p1", "last_edited_time": "2026-09-01T00:00:00Z",
            "properties": {"title": {"type": "title", "title": [{"plain_text": "Settlement TRD"}]}}}]})

    monkeypatch.setattr(NotionConnector, "transport", httpx.MockTransport(handler))
    notion = REGISTRY["notion"]
    result = notion.test({}, {"token": "good"}, settings)
    assert result.ok and "1 page(s)" in result.message and result.details == ["Settlement TRD"]
    assert "invalid token" in notion.test({}, {"token": "bad"}, settings).message


def test_git_test(settings, tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    (origin / "A.kt").write_text("fun a() = 1\n")
    for args in (["init", "-q", "-b", "main"], ["add", "."], ["commit", "-q", "-m", "x"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=origin, check=True)
    git = REGISTRY["git"]
    assert git.test({"url": f"file://{origin}", "auth": "none"}, {}, settings).message.startswith(
        "Repository reachable — branch main")
    assert not git.test({"url": f"file://{tmp_path}/missing", "auth": "none"}, {}, settings).ok


def test_folder_test(settings, tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "trd.md").write_text("# TRD\n")
    folder = REGISTRY["folder"]
    assert folder.test({"path": str(tmp_path / "docs"), "include": ["**/*.md"]}, {}, settings).message == \
        "Found 1 document(s)"
    assert not folder.test({"path": str(tmp_path / "nope")}, {}, settings).ok


# ---- with the local Postgres -----------------------------------------------------------------------


@pytest.mark.integration
def test_postgres_test_blocks_users_that_can_write(settings, fake_db_url):
    from askcite.connectors.base import TestResult
    from tests.conftest import STORE_ADMIN_URL

    postgres = REGISTRY["postgres"]
    read_only: TestResult = postgres.test({"schemas": ["public"]}, {"url": fake_db_url}, settings)
    assert read_only.ok and not read_only.blocked
    owner_url = STORE_ADMIN_URL.rsplit("/", 1)[0] + "/askcite_test_fake"
    owner = postgres.test({"schemas": ["public"]}, {"url": owner_url}, settings)
    assert owner.blocked and "superuser" in owner.message
    allowed = postgres.test({"schemas": ["public"], "allow_writable": True}, {"url": owner_url}, settings)
    assert allowed.ok and any("Not read-only" in w for w in allowed.warnings)


@pytest.mark.integration
def test_saved_connectors_are_encrypted_and_merged_into_sources(settings, store, tmp_path, fake_db_url):
    box = SecretBox.for_data_dir(tmp_path)
    save_connector(store, box, "folder", "docs", {"path": "docs", "include": ["**/*.md"]}, {})
    save_connector(store, box, "postgres", "shop-db", {"schemas": ["public"], "max_rows": 100}, {"url": fake_db_url})
    save_connector(store, box, "notion", "notion", {"root_page_ids": []}, {"token": "secret-notion-token"})
    raw = store.execute("select secrets from connector where name = 'notion'").fetchone()["secrets"]
    assert "secret-notion-token" not in raw
    sources = effective_sources(settings, store, box)
    assert [f.name for f in sources.docs_folders] == ["docs"]
    assert sources.database.name == "shop-db" and sources.database.resolve_url() == fake_db_url  # connector wins
    assert sources.database.max_rows == 100 and sources.notion.resolve_token() == "secret-notion-token"
    assert get_connector(store, "docs").config["path"] == "docs"
    # secrets never show up in reprs/logs
    assert "secret-notion-token" not in repr(sources.notion) and fake_db_url not in repr(sources.database)
