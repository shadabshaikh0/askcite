"""Generic git access: works with GitLab (cloud or self-hosted), GitHub, Bitbucket, Gitea or a local folder.

Askcite keeps its own read-only *mirror* of each repository and always reads
files at an exact commit, so links and line numbers never drift.

Credentials:
- SSH: a deploy key file (`ssh_key_file`), or the key text saved by a connector (`ssh_key`), which is
  written to a private temporary file only while git runs.
- HTTPS: a token from an environment variable (`token_env`) or saved by a connector (`token`),
  handed to git through GIT_ASKPASS. The token is never put in URLs, command lines or logs.
"""

from __future__ import annotations

import fnmatch
import os
import re
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from askcite.config import CodeSource


class GitError(RuntimeError):
    pass


_ASKPASS = """#!/bin/sh
case "$1" in
  Username*) echo "${ASKCITE_GIT_USERNAME:-oauth2}" ;;
  *) echo "$ASKCITE_GIT_TOKEN" ;;
esac
"""


@dataclass
class GitRepo:
    source: CodeSource
    data_dir: Path

    @property
    def mirror(self) -> Path:
        return self.data_dir / "mirrors" / f"{self.source.name}.git"

    # ---- running git -------------------------------------------------------------------------
    @contextmanager
    def _env(self):
        """Environment for one git call. A connector's SSH key lives in a 0600 temp file only meanwhile."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env["GIT_TERMINAL_PROMPT"] = "0"
        key_file = None
        try:
            key_path = Path(self.source.ssh_key_file).expanduser() if self.source.ssh_key_file else None
            if self.source.ssh_key:
                self.data_dir.mkdir(parents=True, exist_ok=True)
                handle, name = tempfile.mkstemp(prefix="git-key-", dir=self.data_dir)
                with os.fdopen(handle, "w") as out:  # mkstemp creates the file readable by us only
                    out.write(self.source.ssh_key.strip() + "\n")
                key_file = key_path = Path(name)
            if key_path:
                env["GIT_SSH_COMMAND"] = (f"ssh -i '{key_path}' -o IdentitiesOnly=yes -o BatchMode=yes "
                                          f"-o StrictHostKeyChecking=accept-new")
            if self.source.token or self.source.token_env:
                token = self.source.resolve_token()
                if not token:
                    raise GitError(f"environment variable {self.source.token_env} is not set")
                askpass = self.data_dir / "git-askpass.sh"
                askpass.parent.mkdir(parents=True, exist_ok=True)
                askpass.write_text(_ASKPASS)
                askpass.chmod(stat.S_IRWXU)
                env["GIT_ASKPASS"] = str(askpass)
                env["ASKCITE_GIT_TOKEN"] = token
            yield env
        finally:
            if key_file is not None:
                key_file.unlink(missing_ok=True)

    def _git(self, *args: str, input_bytes: bytes | None = None, cwd: Path | None = None,
             timeout: int = 1800) -> bytes:
        with self._env() as env:
            try:
                proc = subprocess.run(["git", *args], cwd=cwd, env=env, input=input_bytes,
                                      capture_output=True, timeout=timeout)
            except subprocess.TimeoutExpired as error:
                raise GitError(f"git {args[0]} timed out after {timeout}s") from error
        if proc.returncode != 0:
            message = proc.stderr.decode(errors="replace").strip()
            token = self.source.resolve_token() or ""
            if token:
                message = message.replace(token, "***")
            raise GitError(f"git {args[0]} failed: {message}")
        return proc.stdout

    def ls_remote(self, timeout: int = 30) -> tuple[str | None, str | None]:
        """Check access without cloning. Returns (default branch, its latest commit)."""
        out = self._git("ls-remote", "--symref", self.source.url, "HEAD", timeout=timeout).decode()
        branch = sha = None
        for line in out.splitlines():
            if line.startswith("ref:"):
                branch = line.split()[1].removeprefix("refs/heads/")
            elif line.endswith("\tHEAD"):
                sha = line.split("\t")[0]
        if self.source.branch:
            wanted = self._git("ls-remote", self.source.url, f"refs/heads/{self.source.branch}",
                               timeout=timeout).decode().split()
            if not wanted:
                raise GitError(f"branch {self.source.branch} was not found")
            branch, sha = self.source.branch, wanted[0]
        return branch, sha

    def _in_mirror(self, *args: str, input_bytes: bytes | None = None) -> bytes:
        return self._git("--git-dir", str(self.mirror), *args, input_bytes=input_bytes)

    # ---- sync --------------------------------------------------------------------------------
    def sync(self) -> str:
        """Create or update the mirror. Returns the commit SHA of the tracked branch."""
        if not self.mirror.exists():
            self.mirror.parent.mkdir(parents=True, exist_ok=True)
            self._git("clone", "--mirror", "--quiet", self.source.url, str(self.mirror))
        else:
            self._in_mirror("fetch", "--prune", "--quiet", "origin")
        return self.head_sha()

    def head_sha(self) -> str:
        ref = self.source.branch or self._in_mirror("symbolic-ref", "HEAD").decode().strip()
        return self._in_mirror("rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()

    # ---- reading -----------------------------------------------------------------------------
    def list_files(self, sha: str) -> list[str]:
        names = self._in_mirror("ls-tree", "-r", "--name-only", sha).decode(errors="replace").splitlines()
        return [n for n in names if self._wanted(n)]

    def _wanted(self, path: str) -> bool:
        included = any(fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch("/" + path, pattern)
                       for pattern in self.source.include)
        excluded = any(fnmatch.fnmatch(path, pattern) for pattern in self.source.exclude)
        return included and not excluded

    def read_files(self, sha: str, paths: list[str]) -> dict[str, str]:
        """Read many files at one commit in a single `git cat-file --batch` call."""
        if not paths:
            return {}
        request = "".join(f"{sha}:{p}\n" for p in paths).encode()
        output = self._in_mirror("cat-file", "--batch", input_bytes=request)
        files: dict[str, str] = {}
        offset = 0
        for path in paths:
            header_end = output.index(b"\n", offset)
            header = output[offset:header_end].decode()
            offset = header_end + 1
            if header.endswith("missing"):
                continue
            size = int(header.split()[-1])
            files[path] = output[offset: offset + size].decode(errors="replace")
            offset += size + 1
        return files

    def changed_files(self, old_sha: str, new_sha: str) -> list[str]:
        out = self._in_mirror("diff", "--name-only", old_sha, new_sha).decode(errors="replace")
        return [p for p in out.splitlines() if p]

    # ---- links -------------------------------------------------------------------------------
    def web_base(self) -> str | None:
        if self.source.web_url:
            return self.source.web_url.rstrip("/")
        url = self.source.url
        match = re.match(r"^(?:ssh://)?git@([^:/]+)[:/](.+?)(?:\.git)?/?$", url)
        if match:
            return f"https://{match.group(1)}/{match.group(2)}"
        match = re.match(r"^https?://(?:[^@/]+@)?(.+?)(?:\.git)?/?$", url)
        if match:
            return f"https://{match.group(1)}"
        return None

    def link(self, sha: str, path: str, start: int | None = None, end: int | None = None) -> str | None:
        base = self.web_base()
        if not base:
            return None
        if "github" in base:
            anchor = f"#L{start}-L{end}" if start else ""
            return f"{base}/blob/{sha}/{path}{anchor}"
        if "bitbucket" in base:
            anchor = f"#lines-{start}:{end}" if start else ""
            return f"{base}/src/{sha}/{path}{anchor}"
        anchor = f"#L{start}-{end}" if start else ""  # GitLab (the default)
        return f"{base}/-/blob/{sha}/{path}{anchor}"
