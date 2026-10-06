from __future__ import annotations

from pathlib import Path

from askcite.config import DocsFolderSource, Settings, SourcesConfig
from askcite.connectors.base import Connector, Field, TestResult
from askcite.sources.docs_folder import folder_files


class FolderConnector(Connector):
    kind = "folder"
    title = "Docs folder"
    description = "Markdown documents on disk, e.g. a docs/ folder with PRDs, TRDs and runbooks."
    multiple = True
    fields = [
        Field("path", "Folder path", required=True, placeholder="/srv/docs or ./docs",
              help="Relative paths start from the config folder."),
        Field("include", "Files to read", "list", default=["**/*.md"]),
        Field("web_url", "Web URL for links (optional)",
              placeholder="https://github.com/team/app/blob/main/docs",
              help="Links become <web URL>/<file>#<heading>."),
    ]

    def default_name(self, config: dict) -> str:
        return Path(config.get("path") or "docs").name or "docs"

    def test(self, config: dict, secrets: dict, settings: Settings) -> TestResult:
        root = Path(config.get("path") or "").expanduser()
        if not root.is_absolute():
            root = settings.config_dir / root
        if not root.is_dir():
            return TestResult(False, f"Folder not found: {root}")
        files = folder_files(root, config.get("include") or ["**/*.md"])
        result = TestResult(True, f"Found {len(files)} document(s)", details=[rel for _, rel in files[:5]])
        if not files:
            result.warnings.append("No matching files — check the include patterns.")
        return result

    def apply(self, sources: SourcesConfig, name: str, config: dict, secrets: dict) -> None:
        sources.docs_folders = [f for f in sources.docs_folders if f.name != name] + [DocsFolderSource(
            name=name, path=config["path"], include=config.get("include") or ["**/*.md"],
            web_url=config.get("web_url"), origin="connector")]

    def source_keys(self, name: str) -> list[str]:
        return [f"folder:{name}"]
