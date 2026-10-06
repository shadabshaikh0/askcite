"""Build Askcite's search index from code, Notion, docs folders and the database schema.

Every function takes the *effective* sources (sources.yaml plus saved connectors); when
`sources` is not given, the ones from sources.yaml are used.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from pathlib import Path

import psycopg

from askcite.config import Settings, SourcesConfig
from askcite.schema.catalog import Catalog
from askcite.sources.code_parser import LANGUAGES, parse_source
from askcite.sources.git import GitRepo
from askcite.text import split_identifiers

log = logging.getLogger(__name__)


# ---- code --------------------------------------------------------------------------------------

def index_code(settings: Settings, store: psycopg.Connection, full: bool = False,
               sources: SourcesConfig | None = None, only: str | None = None) -> list[dict]:
    reports = []
    for source in (sources or settings.sources).code:
        if only and source.name != only:
            continue
        repo = GitRepo(source, settings.data_dir)
        sha = repo.sync()
        row = store.execute("select commit_sha from repo_state where repo = %s", (source.name,)).fetchone()
        previous = row["commit_sha"] if row else None
        if previous == sha and not full:
            reports.append({"repo": source.name, "commit": sha, "files": 0, "note": "already up to date",
                            **_code_totals(store, source.name)})
            continue
        all_files = repo.list_files(sha)
        if previous and not full:
            changed = set(repo.changed_files(previous, sha))
            files = [f for f in all_files if f in changed]
            stale_paths = list(changed)
        else:
            files = all_files
            stale_paths = None
        chunks = examples = 0
        with store.transaction():
            if stale_paths is None:
                store.execute("delete from code_chunk where repo = %s", (source.name,))
                store.execute("delete from sql_example where repo = %s", (source.name,))
            else:
                store.execute("delete from code_chunk where repo = %s and path = any(%s)", (source.name, stale_paths))
                store.execute("delete from sql_example where repo = %s and path = any(%s)", (source.name, stale_paths))
            # older chunks keep their own commit; move unchanged ones to the new commit so links stay consistent
            store.execute("update code_chunk set commit_sha = %s where repo = %s", (sha, source.name))
            store.execute("update sql_example set commit_sha = %s where repo = %s", (sha, source.name))
            for batch_start in range(0, len(files), 200):
                batch = files[batch_start: batch_start + 200]
                contents = repo.read_files(sha, [f for f in batch if "." + f.rsplit(".", 1)[-1] in LANGUAGES])
                for path, text in contents.items():
                    parsed = parse_source(path, text)
                    for chunk in parsed.chunks:
                        store.execute(
                            "insert into code_chunk (repo, commit_sha, path, symbol, kind, start_line, end_line, "
                            "content, search) values (%s, %s, %s, %s, %s, %s, %s, %s, "
                            "setweight(to_tsvector('english', %s), 'A') || to_tsvector('english', %s))",
                            (source.name, sha, path, chunk.symbol, chunk.kind, chunk.start_line, chunk.end_line,
                             chunk.content, split_identifiers(chunk.symbol + " " + path.rsplit("/", 1)[-1]),
                             split_identifiers(chunk.content)[:200_000]),
                        )
                        chunks += 1
                    for example in parsed.sql_examples:
                        store.execute(
                            "insert into sql_example (repo, commit_sha, path, symbol, start_line, end_line, sql_text, "
                            "operation, tables, parsed, search) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                            "to_tsvector('english', %s))",
                            (source.name, sha, path, example.symbol, example.start_line, example.end_line,
                             example.sql_text, example.operation, example.tables, example.parsed,
                             split_identifiers(example.symbol + " " + example.sql_text)),
                        )
                        examples += 1
            store.execute(
                "insert into repo_state (repo, commit_sha) values (%s, %s) "
                "on conflict (repo) do update set commit_sha = excluded.commit_sha, indexed_at = now()",
                (source.name, sha),
            )
        reports.append({"repo": source.name, "commit": sha, "files": len(files), "chunks": chunks,
                        "sql_examples": examples, "mode": "full" if stale_paths is None else "changed files",
                        **_code_totals(store, source.name)})
    return reports


def _code_totals(store: psycopg.Connection, repo: str) -> dict:
    row = store.execute("select count(distinct path) as files, count(*) filter (where kind = 'function') as functions "
                        "from code_chunk where repo = %s", (repo,)).fetchone()
    queries = store.execute("select count(*) as n from sql_example where repo = %s", (repo,)).fetchone()["n"]
    return {"total_files": row["files"], "total_functions": row["functions"], "total_sql_examples": queries}


# ---- database schema ---------------------------------------------------------------------------

def index_schema(settings: Settings, store: psycopg.Connection, prefer_file: bool = False,
                 sources: SourcesConfig | None = None) -> dict:
    """Read the schema (names only, never rows) and save a snapshot."""
    database = (sources or settings.sources).database
    if database is None:
        raise ValueError("No database is connected")
    url = database.resolve_url()
    use_file = prefer_file or database.schema_from == "file" or (database.schema_from == "auto" and not url)
    if not use_file:
        if not url:
            raise ValueError(f"schema_from is 'live' but {database.url_env} is not set in .env")
        from askcite.sources.postgres import catalog_from_database
        catalog, origin = catalog_from_database(url, database.schemas, source=database.name), "live"
    elif database.schema_file:
        from askcite.sources.schema_file import catalog_from_file
        path = settings.config_dir / database.schema_file
        catalog, origin = catalog_from_file(path, source=database.name), "file"
    else:
        raise ValueError(f"Set {database.url_env} in .env, connect a database, or set `schema_file`")
    document = json.dumps(catalog.model_dump(), sort_keys=True)
    latest = store.execute("select catalog::text = %s::jsonb::text as same from schema_snapshot where source = %s "
                           "order by taken_at desc, id desc limit 1", (document, database.name)).fetchone()
    changed = not (latest and latest["same"])
    if changed:  # keep a new snapshot only when the schema changed (this also records *when* it changed)
        store.execute("insert into schema_snapshot (source, origin, catalog) values (%s, %s, %s)",
                      (database.name, origin, document))
    return {"source": database.name, "origin": origin, "tables": len(catalog.tables),
            "problems": len(catalog.problems), "changed": changed}


def load_catalog(store: psycopg.Connection, source: str) -> Catalog | None:
    row = store.execute("select catalog from schema_snapshot where source = %s order by taken_at desc, id desc "
                        "limit 1", (source,)).fetchone()
    return Catalog.model_validate(row["catalog"]) if row else None


# ---- Notion ------------------------------------------------------------------------------------

def index_notion(settings: Settings, store: psycopg.Connection, client=None, full: bool = False,
                 sources: SourcesConfig | None = None):
    """Index pages shared with the Notion integration; only pages edited since last time are re-read."""
    from askcite.sources.notion import NotionClient, NotionSyncReport, sections_from_blocks, under_roots

    notion = (sources or settings.sources).notion
    if notion is None:
        raise ValueError("Notion is not connected")
    if client is None:
        token = notion.resolve_token()
        if not token:
            raise ValueError(f"Set {notion.token_env} in .env or connect Notion (integration token)")
        client = NotionClient(token, api_version=notion.api_version)
    report = NotionSyncReport()
    pages = client.pages()
    if notion.root_page_ids:
        pages = under_roots(pages, notion.root_page_ids)
    report.pages_seen = len(pages)
    known = {row["page_id"]: row["last_edited"] for row in store.execute("select * from notion_page_state")}
    for page in pages:
        if not full and known.get(page.id) == page.last_edited:
            continue
        try:
            sections = sections_from_blocks(page, client.blocks(page.id))
        except Exception as error:  # noqa: BLE001 - one broken page must not stop the others
            report.errors.append(f"{page.title}: {error}")
            continue
        with store.transaction():
            store.execute("delete from doc_section where page_id = %s", (page.id,))
            for section in sections:
                _insert_section(store, section)
            store.execute(
                "insert into notion_page_state (page_id, last_edited) values (%s, %s) on conflict (page_id) "
                "do update set last_edited = excluded.last_edited, indexed_at = now()", (page.id, page.last_edited))
        report.pages_indexed += 1
        report.sections += len(sections)
    gone = set(known) - {p.id for p in pages}  # unshared or deleted pages disappear from the index too
    for page_id in gone:
        store.execute("delete from doc_section where page_id = %s", (page_id,))
        store.execute("delete from notion_page_state where page_id = %s", (page_id,))
    report.pages_removed = len(gone)
    return report


def _insert_section(store: psycopg.Connection, section) -> None:
    """Save one document section (Notion or docs folder). Long sections arrive in parts and are joined."""
    store.execute(
        "insert into doc_section (page_id, block_id, page_title, heading_path, url, content, last_edited, "
        "search) values (%s, %s, %s, %s, %s, %s, %s, setweight(to_tsvector('english', %s), 'A') || "
        "to_tsvector('english', %s)) on conflict (page_id, block_id) do update set "
        "content = doc_section.content || E'\\n' || excluded.content, search = doc_section.search || "
        "excluded.search",
        (section.page_id, section.block_id, section.page_title, section.heading_path, section.url or "",
         section.content, section.last_edited, " ".join([section.page_title, *section.heading_path]),
         section.content),
    )


def notion_report_dict(report) -> dict:
    return asdict(report)


# ---- docs folders (Markdown) -------------------------------------------------------------------

def index_docs_folders(settings: Settings, store: psycopg.Connection, sources: SourcesConfig | None = None,
                       only: str | None = None, full: bool = False) -> list[dict]:
    """Index Markdown files; only files whose content changed are re-read, deleted files are removed."""
    from askcite.sources.docs_folder import folder_files, sections_from_markdown

    reports = []
    for folder in (sources or settings.sources).docs_folders:
        if only and folder.name != only:
            continue
        root = Path(folder.path).expanduser()
        if not root.is_absolute():
            root = settings.config_dir / root
        if not root.is_dir():
            raise ValueError(f"docs folder {folder.name}: {root} does not exist")
        prefix = f"folder:{folder.name}/"
        known = {row["page_id"]: row["content_hash"] for row in store.execute(
            "select page_id, content_hash from docs_file_state where page_id like %s", (prefix + "%",))}
        seen, indexed, section_count = set(), 0, 0
        for path, relative in folder_files(root, folder.include):
            page_id = prefix + relative
            seen.add(page_id)
            text = path.read_text(errors="replace")
            digest = hashlib.sha256(text.encode()).hexdigest()
            if not full and known.get(page_id) == digest:
                continue
            sections = sections_from_markdown(page_id, relative, text, folder.web_url)
            with store.transaction():
                store.execute("delete from doc_section where page_id = %s", (page_id,))
                for section in sections:
                    _insert_section(store, section)
                store.execute("insert into docs_file_state (page_id, content_hash) values (%s, %s) on conflict "
                              "(page_id) do update set content_hash = excluded.content_hash, indexed_at = now()",
                              (page_id, digest))
            indexed += 1
            section_count += len(sections)
        gone = set(known) - seen
        for page_id in gone:
            store.execute("delete from doc_section where page_id = %s", (page_id,))
            store.execute("delete from docs_file_state where page_id = %s", (page_id,))
        total = store.execute("select count(*) as n from doc_section where page_id like %s",
                              (prefix + "%",)).fetchone()["n"]
        reports.append({"folder": folder.name, "files": len(seen), "files_indexed": indexed,
                        "sections": section_count, "files_removed": len(gone), "total_sections": total})
    return reports


# ---- removing a source -------------------------------------------------------------------------

def forget_source(store: psycopg.Connection, kind: str, name: str) -> None:
    """Delete everything indexed from one source (used when a connector is removed)."""
    with store.transaction():
        if kind == "git":
            for table in ("code_chunk", "sql_example", "repo_state"):
                store.execute(f"delete from {table} where repo = %s", (name,))  # noqa: S608 - fixed table names
        elif kind == "folder":
            pattern = f"folder:{name}/%"
            store.execute("delete from doc_section where page_id like %s", (pattern,))
            store.execute("delete from docs_file_state where page_id like %s", (pattern,))
        elif kind == "notion":
            store.execute("delete from doc_section where page_id in (select page_id from notion_page_state)")
            store.execute("delete from notion_page_state")
        elif kind == "postgres":
            store.execute("delete from schema_snapshot where source = %s", (name,))
