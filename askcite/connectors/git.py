from __future__ import annotations

import re

from askcite.config import CodeSource, Settings, SourcesConfig
from askcite.connectors.base import Connector, Field, TestResult
from askcite.sources.git import GitError, GitRepo


class GitConnector(Connector):
    kind = "git"
    title = "Code repository"
    description = "Any git host: GitLab, GitHub, Bitbucket, self-hosted, or a local folder."
    multiple = True
    fields = [
        Field("url", "Repository URL", required=True, placeholder="git@gitlab.com:team/app.git",
              help="SSH (git@…), HTTPS (https://…) or a local path (file:///…)."),
        Field("auth", "Authentication", "select", default="none",
              options=[("none", "None (public or local)"), ("ssh_key", "SSH deploy key"),
                       ("https_token", "HTTPS access token")]),
        Field("ssh_key", "SSH deploy key (private key)", "textarea", secret=True, required=True,
              show_if=("auth", "ssh_key"), help="Use a read-only deploy key made only for Askcite."),
        Field("token", "Access token", "password", secret=True, required=True, show_if=("auth", "https_token"),
              help="A read-only token (GitLab: read_repository; GitHub: contents:read)."),
        Field("branch", "Branch", placeholder="default branch"),
        Field("include", "Files to read", "list", default=["**/*.kt", "**/*.java"],
              help="Glob patterns, e.g. src/main/**/*.kt"),
        Field("exclude", "Files to skip", "list", default=["**/test/**", "**/build/**", "**/generated/**"]),
        Field("web_url", "Web URL for links (optional)", placeholder="https://gitlab.com/team/app",
              help="Worked out from the repository URL when empty."),
    ]

    def default_name(self, config: dict) -> str:
        match = re.search(r"([^/:]+?)(?:\.git)?/?$", config.get("url") or "")
        return match.group(1) if match else "repo"

    def _source(self, name: str, config: dict, secrets: dict) -> CodeSource:
        auth = config.get("auth") or "none"
        return CodeSource(
            name=name, url=config["url"], branch=config.get("branch"), web_url=config.get("web_url"),
            token=secrets.get("token") if auth == "https_token" else None,
            ssh_key=secrets.get("ssh_key") if auth == "ssh_key" else None,
            include=config.get("include") or ["**/*.kt", "**/*.java"],
            exclude=config.get("exclude") or [], origin="connector",
        )

    def test(self, config: dict, secrets: dict, settings: Settings) -> TestResult:
        repo = GitRepo(self._source(self.default_name(config), config, secrets), settings.data_dir)
        try:
            branch, sha = repo.ls_remote()
        except GitError as error:
            return TestResult(False, str(error))
        return TestResult(True, f"Repository reachable — branch {branch} at {sha[:10] if sha else '?'}",
                          details=[f"Links will look like: {repo.link(sha or 'HEAD', 'path/File.kt', 10, 20)}"])

    def apply(self, sources: SourcesConfig, name: str, config: dict, secrets: dict) -> None:
        sources.code = [s for s in sources.code if s.name != name] + [self._source(name, config, secrets)]

    def source_keys(self, name: str) -> list[str]:
        return [f"git:{name}"]
