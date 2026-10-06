"""Loads Askcite settings.

Secrets come from environment variables (usually a `.env` file you fill in yourself).
Everything else comes from three YAML files in the config directory:

- sources.yaml  — where code, Notion docs and the database live, and which AI may see what
- access.yaml   — which Slack channels / people may ask which kinds of questions
- glossary.yaml — plain-English meaning of tables, columns and business terms
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

Origin = Literal["yaml", "connector"]  # where a source was defined: sources.yaml or a saved connector


class CodeSource(BaseModel):
    name: str
    url: str  # file:///path, git@host:group/repo.git, ssh://..., https://...
    branch: str | None = None  # default: the remote's default branch
    web_url: str | None = None  # e.g. https://gitlab.com/group/repo — used for clickable links
    ssh_key_file: str | None = None
    token_env: str | None = None  # name of env var holding an HTTPS token (never the token itself)
    token: str | None = Field(None, repr=False)  # HTTPS token saved by a connector (stored encrypted)
    ssh_key: str | None = Field(None, repr=False)  # SSH private key text saved by a connector (stored encrypted)
    include: list[str] = Field(default_factory=lambda: ["**/*.kt", "**/*.java"])
    exclude: list[str] = Field(default_factory=lambda: ["**/test/**", "**/build/**", "**/generated/**"])
    origin: Origin = "yaml"

    def resolve_token(self) -> str | None:
        return self.token or (os.environ.get(self.token_env) if self.token_env else None)


class NotionSource(BaseModel):
    token_env: str = "NOTION_TOKEN"
    token: str | None = Field(None, repr=False)
    root_page_ids: list[str] = Field(default_factory=list)  # empty = every page shared with the integration
    api_version: str = "2022-06-28"
    origin: Origin = "yaml"

    def resolve_token(self) -> str | None:
        return self.token or os.environ.get(self.token_env)


class DocsFolderSource(BaseModel):
    """A folder of Markdown documents (e.g. docs/ in a repo), split into sections by heading."""
    name: str
    path: str  # relative to the config folder, or absolute
    include: list[str] = Field(default_factory=lambda: ["**/*.md"])
    web_url: str | None = None  # base URL for links, e.g. https://github.com/org/repo/blob/main/docs
    origin: Origin = "yaml"


class SlackSource(BaseModel):
    app_token_env: str = "SLACK_APP_TOKEN"
    bot_token_env: str = "SLACK_BOT_TOKEN"
    app_token: str | None = Field(None, repr=False)
    bot_token: str | None = Field(None, repr=False)
    allowed_channels: list[str] = Field(default_factory=list)  # added to access.yaml
    data_users: list[str] = Field(default_factory=list)  # Slack user ids added to the "data" group
    approvers: list[str] = Field(default_factory=list)  # Slack user ids added to the "approvers" group
    origin: Origin = "yaml"

    def resolve_tokens(self) -> tuple[str | None, str | None]:
        return (self.app_token or os.environ.get(self.app_token_env),
                self.bot_token or os.environ.get(self.bot_token_env))


class DatabaseSource(BaseModel):
    name: str = "db"
    url_env: str = "READONLY_DB_URL"
    url: str | None = Field(None, repr=False)  # connection URL saved by a connector (stored encrypted)
    schema_file: str | None = None  # schema-only dump or DDL file (no data)
    # Where the table/column list comes from: "live" = the database at url_env, "file" = schema_file,
    # "auto" = live when url_env is set, otherwise the file. Use "file" while url_env points at the FAKE
    # test database, which keeps tables and columns but not CHECK lists or keys.
    schema_from: Literal["auto", "live", "file"] = "auto"
    schemas: list[str] = Field(default_factory=lambda: ["public"])
    max_rows: int = 500
    timeout_seconds: int = 15
    blocked_tables: list[str] = Field(default_factory=list)
    approval_tables: list[str] = Field(default_factory=list)  # queries touching these wait for an approver
    extra_sensitive_columns: list[str] = Field(default_factory=list)  # "table.column" or "column"
    allowed_sensitive_columns: list[str] = Field(default_factory=list)  # overrides for false positives
    origin: Origin = "yaml"

    def resolve_url(self) -> str | None:
        return self.url or os.environ.get(self.url_env)


Where = Literal["cloud", "local", "none"]


class AiPolicy(BaseModel):
    """Which AI model may read which kind of content.

    `data_values` (rows returned by live queries) can never go to the cloud model.
    """

    docs: Where = "cloud"
    code: Where = "cloud"
    schema_: Where = Field("cloud", alias="schema")
    data_values: Literal["local", "none"] = "none"

    model_config = {"populate_by_name": True}


class AiSettings(BaseModel):
    cloud_model: str = "anthropic/claude-sonnet-5"
    fallback_models: list[str] = Field(default_factory=list)  # used when cloud_model is overloaded
    local_model: str | None = None  # e.g. "ollama/qwen2.5:14b"
    local_api_base: str | None = None  # e.g. "http://ollama:11434"
    policy: AiPolicy = Field(default_factory=AiPolicy)


class Limits(BaseModel):
    max_tool_calls: int = 8
    max_queries: int = 2
    max_seconds: int = 60


class DemoSettings(BaseModel):
    """Public demo mode (`askcite run --public-demo`): open read-only pages with limits."""
    suggested_questions: list[str] = Field(default_factory=list)
    banner: str = "Live demo · all data is made up (a fake shop)"
    source_url: str | None = None  # e.g. the GitHub repository
    per_visitor_limit: int = 5  # questions per visitor (IP) per window
    per_visitor_window_minutes: int = 10
    daily_limit: int = 150  # questions per day for the whole site (answers from the cache don't count)
    max_question_chars: int = 300
    cache_hours: int = 24  # identical questions get the stored answer for this long


class SourcesConfig(BaseModel):
    timezone: str = "Asia/Kolkata"
    code: list[CodeSource] = Field(default_factory=list)
    notion: NotionSource | None = None
    docs_folders: list[DocsFolderSource] = Field(default_factory=list)
    database: DatabaseSource | None = None
    slack: SlackSource | None = None
    ai: AiSettings = Field(default_factory=AiSettings)
    limits: Limits = Field(default_factory=Limits)
    demo: DemoSettings = Field(default_factory=DemoSettings)


class Group(BaseModel):
    all_users: bool = False
    slack_users: list[str] = Field(default_factory=list)
    slack_usergroups: list[str] = Field(default_factory=list)


class AccessConfig(BaseModel):
    allowed_channels: list[str] = Field(default_factory=list)
    allow_dms: bool = True
    groups: dict[str, Group] = Field(
        default_factory=lambda: {"everyone": Group(all_users=True), "data": Group(), "approvers": Group()}
    )


class TableNote(BaseModel):
    description: str = ""
    columns: dict[str, str] = Field(default_factory=dict)


class Glossary(BaseModel):
    tables: dict[str, TableNote] = Field(default_factory=dict)
    terms: dict[str, str] = Field(default_factory=dict)

    @field_validator("tables", mode="before")
    @classmethod
    def _allow_plain_strings(cls, value):
        # `orders: "one row per customer order"` is accepted as shorthand for a description
        if isinstance(value, dict):
            return {k: ({"description": v} if isinstance(v, str) else v) for k, v in value.items()}
        return value


class Settings(BaseModel):
    config_dir: Path
    data_dir: Path
    store_url: str
    sources: SourcesConfig
    access: AccessConfig
    glossary: Glossary

    def secret(self, env_name: str | None) -> str | None:
        return os.environ.get(env_name) if env_name else None


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open() as handle:
        return yaml.safe_load(handle) or {}


def load_dotenv(path: Path) -> None:
    """Minimal .env reader: KEY=VALUE lines; existing environment variables win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    config_dir = Path(os.environ.get("ASKCITE_CONFIG_DIR", "config")).expanduser().resolve()
    load_dotenv(Path(".env"))
    load_dotenv(config_dir / ".env")
    sources = SourcesConfig.model_validate(_read_yaml(config_dir / "sources.yaml"))
    if os.environ.get("ASKCITE_MODEL"):  # e.g. a hosted demo switching to gemini/gemini-flash-latest
        sources.ai.cloud_model = os.environ["ASKCITE_MODEL"]
    if os.environ.get("ASKCITE_FALLBACK_MODELS"):  # comma-separated, e.g. gemini/gemini-flash-lite-latest
        sources.ai.fallback_models = [m.strip() for m in os.environ["ASKCITE_FALLBACK_MODELS"].split(",") if m.strip()]
    return Settings(
        config_dir=config_dir,
        data_dir=Path(os.environ.get("ASKCITE_DATA_DIR", "data")).expanduser().resolve(),
        store_url=os.environ.get("ASKCITE_STORE_URL", "postgresql://askcite:askcite@localhost:5433/askcite"),
        sources=sources,
        access=AccessConfig.model_validate(_read_yaml(config_dir / "access.yaml")),
        glossary=Glossary.model_validate(_read_yaml(config_dir / "glossary.yaml")),
    )
