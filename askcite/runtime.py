"""The running Askcite: settings + saved connectors -> sources -> workspace, plus background sync.

Used by `askcite run` (web page + Slack bot) and by the CLI commands, so they all see
the same sources: sources.yaml merged with the connectors saved in the database.
"""

from __future__ import annotations

import logging
import threading

import psycopg

from askcite.config import Settings, SourcesConfig
from askcite.connectors import effective_sources
from askcite.connectors.store import finish_sync, start_sync
from askcite.secrets import SecretBox
from askcite.tools import Workspace

log = logging.getLogger("askcite")


class Runtime:
    def __init__(self, settings: Settings, store: psycopg.Connection | None = None):
        from askcite.storage.db import connect

        self.settings = settings
        self.store = store if store is not None else connect(settings.store_url)
        self.box = SecretBox.for_data_dir(settings.data_dir)
        self.sources = effective_sources(settings, self.store, self.box)
        self.workspace = Workspace(settings.model_copy(update={"sources": self.sources}), self.store, None, None)
        self.workspace.apply_sources(self.sources)
        self.wake = threading.Event()  # set by "Sync now" to start a refresh round immediately
        self.slack = None  # SlackManager, when the bot runs
        self._skipped_logged: set[str] = set()
        self._brain = None

    @property
    def brain(self):
        if self._brain is None:
            from askcite.brain import Brain
            from askcite.llm import models_from_settings

            cloud, local = models_from_settings(self.settings.sources.ai)
            self._brain = Brain(self.workspace, cloud, local)
        return self._brain

    def reload_sources(self) -> SourcesConfig:
        """Re-read connectors and apply them (new repos, a changed database, new Slack tokens...)."""
        self.sources = effective_sources(self.settings, self.store, self.box)
        self.workspace.apply_sources(self.sources)
        if self.slack is not None:
            self.slack.ensure(self.sources.slack)
        return self.sources

    def sync(self, only: str | None = None) -> dict[str, str]:
        from askcite.storage.db import connect

        with connect(self.settings.store_url) as store:  # own connection: answering continues meanwhile
            results = refresh_once(self.settings, self.workspace, store, self.sources, self._skipped_logged,
                                   only=only)
        self.workspace.reload_catalog()
        return results

    def request_sync(self) -> None:
        self.wake.set()

    def refresh_loop(self, minutes: int) -> None:
        while True:
            try:
                self.reload_sources()
                self.sync()
            except Exception:  # noqa: BLE001 - e.g. the storage database is down; the next round retries
                log.exception("background refresh could not run")
            self.wake.wait(timeout=max(1, minutes) * 60)
            self.wake.clear()


def refresh_steps(settings: Settings, workspace: Workspace, store, sources: SourcesConfig) -> list[tuple[str, object]]:
    """(source key, function or None if not configured) for every source."""
    from askcite import indexing

    steps: list[tuple[str, object]] = []
    for repo in sources.code:
        steps.append((f"git:{repo.name}",
                      lambda name=repo.name: indexing.index_code(settings, store, sources=sources, only=name)))
    if sources.notion:
        steps.append(("notion", (lambda: indexing.index_notion(settings, store, sources=sources))
                      if sources.notion.resolve_token() else None))
    for folder in sources.docs_folders:
        steps.append((f"folder:{folder.name}", lambda name=folder.name: indexing.index_docs_folders(
            settings, store, sources=sources, only=name)))
    if sources.database:
        database = sources.database
        has_url, has_file = bool(database.resolve_url()), bool(database.schema_file)
        configured = {"live": has_url, "file": has_file, "auto": has_url or has_file}[database.schema_from]
        steps.append((f"schema:{database.name}",
                      (lambda: _refresh_schema(settings, workspace, store, sources)) if configured else None))
    return steps


def refresh_once(settings: Settings, workspace: Workspace, store, sources: SourcesConfig,
                 skipped_logged: set[str], only: str | None = None) -> dict[str, str]:
    """Sync every source independently: one failing or unconfigured source never blocks the others."""
    results: dict[str, str] = {}
    record = store is not None and hasattr(store, "transaction")  # tests pass simple fakes
    for key, step in refresh_steps(settings, workspace, store, sources):
        if only and key != only:
            continue
        if step is None:
            results[key] = "skipped (not configured)"
            if key not in skipped_logged:  # say it once, not every round
                log.info("%s is not configured (missing token/URL), skipping it", key)
                skipped_logged.add(key)
            continue
        run_id = start_sync(store, key) if record else None
        try:
            report = step()
            results[key] = "ok"
            if run_id:
                finish_sync(store, run_id, "ok", _stats(report))
        except Exception as error:  # noqa: BLE001 - log and carry on with the other sources
            log.exception("refreshing %s failed", key)
            results[key] = "failed"
            if run_id:
                finish_sync(store, run_id, "failed", error=str(error).strip().splitlines()[0][:500])
    return results


def _stats(report) -> dict:
    from dataclasses import asdict, is_dataclass

    if is_dataclass(report):
        return asdict(report)
    if isinstance(report, list):
        return report[0] if len(report) == 1 and isinstance(report[0], dict) else {"items": report}
    return report if isinstance(report, dict) else {}


def _refresh_schema(settings: Settings, workspace: Workspace, store, sources: SourcesConfig) -> dict:
    from askcite import indexing

    report = indexing.index_schema(settings, store, sources=sources)
    if workspace.store is not None:
        workspace.reload_catalog()
    else:  # tests: no shared store, load straight from the given one
        workspace.catalog = indexing.load_catalog(store, sources.database.name)
        if workspace.catalog is not None:
            workspace._build_guard()
    return report
