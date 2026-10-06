import os
import subprocess

import pytest

from askcite.config import CodeSource
from askcite.sources.git import GitError, GitRepo


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True,
                   capture_output=True)


@pytest.fixture()
def origin(tmp_path):
    repo = tmp_path / "origin"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "A.kt").write_text("fun a() = 1\n")
    (repo / "src" / "test").mkdir()
    (repo / "src" / "test" / "ATest.kt").write_text("fun t() = 1\n")
    (repo / "README.md").write_text("hi\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "first")
    return repo


def test_mirror_list_read_and_diff(origin, tmp_path):
    repo = GitRepo(CodeSource(name="demo", url=f"file://{origin}"), tmp_path / "data")
    first = repo.sync()
    assert repo.list_files(first) == ["src/A.kt"]  # tests and non-code files are excluded by default
    assert repo.read_files(first, ["src/A.kt"]) == {"src/A.kt": "fun a() = 1\n"}
    (origin / "src" / "B.kt").write_text("fun b() = 2\n")
    git(origin, "add", ".")
    git(origin, "commit", "-q", "-m", "second")
    second = repo.sync()
    assert second != first and repo.changed_files(first, second) == ["src/B.kt"]
    assert repo.read_files(first, ["src/B.kt"]) == {}  # reading is always at an exact commit


def test_links_for_common_hosts(tmp_path):
    gitlab = GitRepo(CodeSource(name="x", url="git@gitlab.com:team/app.git"), tmp_path)
    assert gitlab.link("abc", "src/A.kt", 3, 9) == "https://gitlab.com/team/app/-/blob/abc/src/A.kt#L3-9"
    github = GitRepo(CodeSource(name="x", url="https://github.com/team/app.git"), tmp_path)
    assert github.link("abc", "src/A.kt", 3, 9) == "https://github.com/team/app/blob/abc/src/A.kt#L3-L9"
    local = GitRepo(CodeSource(name="x", url="file:///tmp/app", web_url="https://git.corp/team/app/"), tmp_path)
    assert local.link("abc", "a.kt", 1, 2) == "https://git.corp/team/app/-/blob/abc/a.kt#L1-2"
    assert GitRepo(CodeSource(name="x", url="file:///tmp/app"), tmp_path).link("abc", "a.kt") is None


def test_https_token_comes_from_env_and_never_hits_disk(tmp_path, monkeypatch):
    repo = GitRepo(CodeSource(name="x", url="https://git.example/app.git", token_env="DEMO_GIT_TOKEN"), tmp_path)
    with pytest.raises(GitError), repo._env():
        pass
    monkeypatch.setenv("DEMO_GIT_TOKEN", "s3cr3t-token")
    with repo._env() as env:
        assert env["ASKCITE_GIT_TOKEN"] == "s3cr3t-token"
    assert "s3cr3t-token" not in (tmp_path / "git-askpass.sh").read_text()


def test_connector_ssh_key_exists_only_while_git_runs(tmp_path):
    repo = GitRepo(CodeSource(name="x", url="git@git.example:app.git", ssh_key="-----BEGIN KEY-----\nabc\n"),
                   tmp_path)
    with repo._env() as env:
        key_path = env["GIT_SSH_COMMAND"].split("'")[1]
        assert oct(os.stat(key_path).st_mode)[-3:] == "600"
        assert open(key_path).read().startswith("-----BEGIN KEY-----")
    assert not os.path.exists(key_path)


def test_ls_remote_reports_default_branch_and_commit(origin, tmp_path):
    branch, sha = GitRepo(CodeSource(name="demo", url=f"file://{origin}"), tmp_path).ls_remote()
    assert branch == "main" and len(sha) == 40
    with pytest.raises(GitError):
        GitRepo(CodeSource(name="demo", url=f"file://{origin}", branch="nope"), tmp_path).ls_remote()
