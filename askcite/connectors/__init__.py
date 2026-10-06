"""Connectors: Slack, Notion, code repositories, PostgreSQL and docs folders.

`effective_sources()` merges what is in sources.yaml with the connectors saved from the web
page or `askcite connect`. When both define the same thing (e.g. a database), the connector wins.
"""

from __future__ import annotations

import logging

import psycopg

from askcite.config import Settings, SlackSource, SourcesConfig
from askcite.connectors.base import Connector, Field, TestResult
from askcite.connectors.folder import FolderConnector
from askcite.connectors.git import GitConnector
from askcite.connectors.notion import NotionConnector
from askcite.connectors.postgres import PostgresConnector
from askcite.connectors.slack import SlackConnector
from askcite.connectors.store import list_connectors
from askcite.secrets import SecretBox, SecretsUnavailable

log = logging.getLogger(__name__)

REGISTRY: dict[str, Connector] = {c.kind: c for c in (
    SlackConnector(), NotionConnector(), GitConnector(), PostgresConnector(), FolderConnector())}

__all__ = ["REGISTRY", "Connector", "Field", "TestResult", "effective_sources"]


def effective_sources(settings: Settings, store: psycopg.Connection | None, box: SecretBox | None) -> SourcesConfig:
    sources = settings.sources.model_copy(deep=True)
    if sources.slack is None:
        env_slack = SlackSource()
        if all(env_slack.resolve_tokens()):  # SLACK_APP_TOKEN / SLACK_BOT_TOKEN in .env still work
            sources.slack = env_slack
    if store is None or box is None:
        return sources
    for record in list_connectors(store):
        connector = REGISTRY.get(record.kind)
        if connector is None or not record.enabled:
            continue
        try:
            secrets = box.decrypt(record.secrets_encrypted)
        except SecretsUnavailable:
            log.error("connector %s: saved secrets cannot be read (secret key changed?) — re-enter them",
                      record.name)
            continue
        connector.apply(sources, record.name, record.config, secrets)
    return sources
