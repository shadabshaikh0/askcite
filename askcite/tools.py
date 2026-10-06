"""The tools the AI may use while answering one question.

Every tool result carries source ids (D1 = document section, C2 = code, S3 = SQL
example from code, T4 = table, Q1 = live query). The final answer must cite them,
and the Slack reply turns them into links.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from askcite.config import Settings
from askcite.data.runner import QueryResult, ReadOnlyRunner, audit
from askcite.safety.pii import SensitiveColumns
from askcite.safety.sql_guard import SqlGuard
from askcite.schema.catalog import Catalog
from askcite.search import read_code, search_code, search_docs, sql_examples, values_seen_in_code
from askcite.sources.git import GitRepo


@dataclass
class Source:
    id: str
    kind: str  # doc | code | sql_example | table | query
    title: str
    url: str | None = None
    detail: str = ""


@dataclass
class Asker:
    user_id: str | None = None
    groups: set[str] = field(default_factory=lambda: {"everyone"})

    @property
    def can_query_data(self) -> bool:
        return "data" in self.groups


@dataclass
class Workspace:
    """Everything the tools need, created once and shared by all questions."""
    settings: Settings
    store: psycopg.Connection | None
    catalog: Catalog | None
    runner: ReadOnlyRunner | None
    repos: dict[str, GitRepo] = field(default_factory=dict)
    guard: SqlGuard | None = None
    sensitive: SensitiveColumns | None = None

    def __post_init__(self):
        if self.catalog is not None and self.settings.sources.database is not None and self.guard is None:
            self._build_guard()
        if not self.repos:
            self.repos = {s.name: GitRepo(s, self.settings.data_dir) for s in self.settings.sources.code}

    def _build_guard(self) -> None:
        database = self.settings.sources.database
        self.sensitive = SensitiveColumns(self.catalog, database.extra_sensitive_columns,
                                          database.allowed_sensitive_columns)
        self.guard = SqlGuard(self.catalog, self.sensitive, database.schemas, database.blocked_tables,
                              database.approval_tables)

    def apply_sources(self, sources) -> None:
        """Switch to a new set of sources (e.g. after a connector was saved) without restarting."""
        from askcite.data.runner import ReadOnlyRunner as Runner

        self.settings = self.settings.model_copy(update={"sources": sources})
        self.repos = {s.name: GitRepo(s, self.settings.data_dir) for s in sources.code}
        database = sources.database
        url = database.resolve_url() if database else None
        self.runner = Runner(url, database.max_rows, database.timeout_seconds, database.schemas) if url else None
        self.reload_catalog()

    def reload_catalog(self) -> None:
        from askcite.indexing import load_catalog

        database = self.settings.sources.database
        if database is None or self.store is None:
            self.catalog, self.guard, self.sensitive = None, None, None
            return
        self.catalog = load_catalog(self.store, database.name)
        if self.catalog is None:
            self.guard, self.sensitive = None, None
        else:
            self._build_guard()


@dataclass
class Session:
    """State for one question."""
    workspace: Workspace
    asker: Asker
    question: str
    model_sees_data: bool
    question_id: int | None = None
    sources: dict[str, Source] = field(default_factory=dict)
    results: dict[str, QueryResult] = field(default_factory=dict)
    queries: dict[str, dict] = field(default_factory=dict)
    code_refs: dict[str, int] = field(default_factory=dict)
    tool_calls: int = 0
    queries_run: int = 0
    steps: list[dict] = field(default_factory=list)  # "how Askcite found this" (never data values)
    _counters: dict[str, int] = field(default_factory=dict)

    def new_id(self, prefix: str) -> str:
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"{prefix}{self._counters[prefix]}"

    def register(self, prefix: str, kind: str, title: str, url: str | None, detail: str = "") -> str:
        for existing in self.sources.values():
            if existing.kind == kind and existing.title == title and existing.detail == detail:
                return existing.id
        source_id = self.new_id(prefix)
        self.sources[source_id] = Source(source_id, kind, title, url, detail)
        return source_id

    # ---------------------------------------------------------------------------------------------
    def tz(self) -> str:
        return self.workspace.settings.sources.timezone

    def code_link(self, row: dict) -> str | None:
        repo = self.workspace.repos.get(row["repo"])
        return repo.link(row["commit_sha"], row["path"], row["start_line"], row["end_line"]) if repo else None

    # ---- docs -----------------------------------------------------------------------------------
    def search_docs(self, query: str) -> dict:
        store = self.workspace.store
        rows = search_docs(store, query) if store else []
        items = []
        for row in rows:
            section = " › ".join(row["heading_path"]) or "(top of page)"
            source_id = self.register("D", "doc", f"{row['page_title']} › {section}", row["url"])
            items.append({"id": source_id, "page": row["page_title"], "section": section,
                          "last_edited": str(row["last_edited"] or ""), "text": row["content"][:1800]})
        return {"results": items} if items else {"results": [], "note": "no matching document sections"}

    # ---- code -----------------------------------------------------------------------------------
    def search_code(self, query: str) -> dict:
        store = self.workspace.store
        rows = search_code(store, query) if store else []
        items = []
        for row in rows:
            file_name = row["path"].rsplit("/", 1)[-1]
            source_id = self.register("C", "code", f"{file_name} › {row['symbol']}", self.code_link(row),
                                      f"lines {row['start_line']}–{row['end_line']}")
            self.code_refs[source_id] = row["id"]
            items.append({"id": source_id, "name": row["symbol"], "file": row["path"],
                          "lines": f"{row['start_line']}-{row['end_line']}", "preview": row["preview"][:900]})
        return {"results": items} if items else {"results": [], "note": "no matching code"}

    def read_code(self, source_id: str) -> dict:
        chunk_id = self.code_refs.get(source_id)
        row = read_code(self.workspace.store, chunk_id) if chunk_id and self.workspace.store else None
        if row is None:
            return {"error": f"unknown code id {source_id}; use an id returned by search_code"}
        lines = row["content"].splitlines()[:300]
        numbered = "\n".join(f"{row['start_line'] + i}: {line}" for i, line in enumerate(lines))
        return {"id": source_id, "name": row["symbol"], "file": row["path"], "code": numbered}

    # ---- database schema ------------------------------------------------------------------------
    def _need_data_access(self) -> dict | None:
        if not self.asker.can_query_data:
            return {"error": "this person is not allowed to ask database questions"}
        if self.workspace.catalog is None:
            return {"error": "no database schema is loaded (run `askcite index schema`)"}
        return None

    def find_tables(self, query: str) -> dict:
        if (problem := self._need_data_access()):
            return problem
        glossary = self.workspace.settings.glossary
        items = []
        for table, _ in self.workspace.catalog.find_tables(query, glossary, limit=8):
            note = glossary.tables.get(table.name)
            source_id = self.register("T", "table", f"table {table.name}", None)
            items.append({"id": source_id, "table": table.name,
                          "meaning": (note.description if note else "") or (table.comment or ""),
                          "columns": [c.name for c in table.columns][:30]})
        return {"results": items} if items else {"results": [], "note": "no matching tables; try other words"}

    def describe_table(self, table_name: str) -> dict:
        if (problem := self._need_data_access()):
            return problem
        table = self.workspace.catalog.table(table_name)
        if table is None:
            return {"error": f"unknown table {table_name}; use find_tables"}
        note = self.workspace.settings.glossary.tables.get(table.name)
        hidden = self.workspace.sensitive.for_table(table) if self.workspace.sensitive else {}
        source_id = self.register("T", "table", f"table {table.name}", None)
        text_columns = [c.name for c in table.columns if not c.allowed_values and c.name.lower() not in hidden
                        and c.type.startswith(("text", "varchar", "character", "char"))]
        seen = values_seen_in_code(self.workspace.store, table.name, text_columns) if self.workspace.store else {}
        columns = []
        for column in table.columns:
            if column.name.lower() in hidden:
                continue
            entry: dict[str, Any] = {"name": column.name, "type": column.type}
            meaning = (note.columns.get(column.name) if note else None) or column.comment
            if meaning:
                entry["meaning"] = meaning
            if column.allowed_values:
                entry["allowed_values"] = column.allowed_values
            elif seen.get(column.name):
                entry["values_used_in_code"] = seen[column.name]
            columns.append(entry)
        return {
            "id": source_id, "table": table.name,
            "meaning": (note.description if note else "") or (table.comment or ""),
            "columns": columns,
            "hidden_personal_columns": sorted(hidden),
            "primary_key": table.primary_key,
            "links_to": [{"columns": fk.columns, "table": fk.ref_table, "ref_columns": fk.ref_columns}
                         for fk in table.foreign_keys],
            "approx_rows": table.estimated_rows,
        }

    def sql_examples(self, tables: list[str], question: str = "") -> dict:
        if (problem := self._need_data_access()):
            return problem
        store = self.workspace.store
        rows = sql_examples(store, tables, question or self.question) if store else []
        items = []
        for row in rows:
            file_name = row["path"].rsplit("/", 1)[-1]
            source_id = self.register("S", "sql_example", f"{file_name} › {row['symbol']}", self.code_link(row),
                                      f"lines {row['start_line']}–{row['end_line']}")
            items.append({"id": source_id, "used_in": row["symbol"], "sql": row["sql_text"][:1500]})
        return {"results": items} if items else {"results": [], "note": "no queries in the code use these tables"}

    # ---- live query ------------------------------------------------------------------------------
    def run_query(self, sql: str, purpose: str = "") -> dict:
        if (problem := self._need_data_access()):
            return problem
        limits = self.workspace.settings.sources.limits
        if self.queries_run >= limits.max_queries:
            return {"status": "not_run", "error": f"query limit reached ({limits.max_queries} per question)"}
        guard = self.workspace.guard
        store = self.workspace.store
        checked = guard.check(sql)
        common = dict(question=self.question, slack_user=self.asker.user_id, question_id=self.question_id)
        if not checked.ok:
            audit(store, sql_text=checked.sql, tables=checked.tables, status="blocked",
                  error="; ".join(checked.errors), **common)
            return {"status": "blocked", "reasons": checked.errors,
                    "hint": "fix the query and try again (use describe_table for exact column names)"}
        query_id = self.new_id("Q")
        if checked.needs_approval:
            audit_id = audit(store, sql_text=checked.sql, tables=checked.tables, status="waiting_approval", **common)
            self.queries[query_id] = {"sql": checked.sql, "tables": checked.tables, "status": "waiting_approval",
                                      "audit_id": audit_id, "purpose": purpose}
            self.sources[query_id] = Source(query_id, "query", "Database query (waiting for approval)", None, purpose)
            return {"id": query_id, "status": "waiting_for_approval",
                    "note": f"An approver must allow this query. Write the final answer using {{{{{query_id}}}}} "
                            "placeholders; they will be filled after approval."}
        if self.workspace.runner is None:
            return {"status": "failed", "error": "the read-only database is not configured (READONLY_DB_URL)"}
        self.queries_run += 1
        try:
            result = self.workspace.runner.run(checked.sql)
        except Exception as error:  # noqa: BLE001 - report the database's message back to the AI
            message = str(error).strip().splitlines()[0][:300]
            audit(store, sql_text=checked.sql, tables=checked.tables, status="failed", error=message, **common)
            if not self.model_sees_data:
                # errors such as `invalid input syntax for type integer: "SETTLED"` can quote a stored value
                message = re.sub(r"\"[^\"]*\"|'[^']*'", "…", message)
            return {"status": "failed", "error": message}
        audit(store, sql_text=checked.sql, tables=checked.tables, status="ok", row_count=result.row_count,
              duration_ms=result.duration_ms, **common)
        self.results[query_id] = result
        ran_at = datetime.now(UTC).astimezone(ZoneInfo(self.tz())).strftime("%d %b %Y, %H:%M")
        self.queries[query_id] = {"sql": checked.sql, "tables": checked.tables, "status": "ok", "purpose": purpose}
        self.sources[query_id] = Source(query_id, "query", "Database query (read-only replica)", None,
                                        f"run {ran_at} · {result.row_count} row(s)")
        answer: dict[str, Any] = {"id": query_id, "status": "ok", "result_shape": result.shape()}
        if self.model_sees_data:
            answer["rows"] = [[str(v) for v in row] for row in result.rows[:50]]
        else:
            answer["note"] = (f"You cannot see the values. In the final answer write {{{{{query_id}}}}} for a single "
                              f"value, {{{{{query_id}.count}}}} for the number of rows, {{{{{query_id}.<column>}}}} "
                              f"for a column of the first row, or {{{{{query_id}.table}}}} for the whole table.")
        return answer


# ---- tool definitions (OpenAI/LiteLLM function-calling format) ----------------------------------------

def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties, "required": required}}}


DOC_TOOLS = [
    _tool("search_docs", "Search the documents (PRDs, TRDs, runbooks — from Notion or docs folders) for a topic.",
          {"query": {"type": "string", "description": "a few keywords"}}, ["query"]),
]
CODE_TOOLS = [
    _tool("search_code", "Search the source code for functions/classes about a topic.",
          {"query": {"type": "string", "description": "keywords or a function/class name"}}, ["query"]),
    _tool("read_code", "Read the full code of a result returned by search_code, with line numbers.",
          {"id": {"type": "string", "description": "a C-id from search_code, e.g. C2"}}, ["id"]),
]
DATA_TOOLS = [
    _tool("find_tables", "Find database tables related to a topic (by table/column names and the glossary).",
          {"query": {"type": "string"}}, ["query"]),
    _tool("describe_table", "Show a table's columns, types, allowed status values and links to other tables.",
          {"table": {"type": "string"}}, ["table"]),
    _tool("sql_examples", "Show real SQL queries from the codebase that use these tables. Copy their joins and "
                          "status values when writing new SQL.",
          {"tables": {"type": "array", "items": {"type": "string"}}}, ["tables"]),
    _tool("run_query", "Run ONE read-only PostgreSQL SELECT on the live database. Personal columns are blocked.",
          {"sql": {"type": "string"}, "purpose": {"type": "string", "description": "what this query answers"}},
          ["sql", "purpose"]),
]
FINAL_TOOL = _tool(
    "final_answer",
    "Give the final answer. Call this exactly once, at the end.",
    {
        "answer": {"type": "string", "description": "short plain-English answer for a non-technical reader; "
                                                    "use {{Q1}}-style placeholders for query results"},
        "how": {"type": "string", "description": "one sentence: how you found it, in plain words"},
        "sources": {"type": "array", "items": {"type": "string"}, "description": "ids you relied on, e.g. D1, C2, Q1"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "found": {"type": "boolean", "description": "false if the docs, code and data did not contain the answer"},
    },
    ["answer", "sources", "confidence", "found"],
)


def call_tool(session: Session, name: str, arguments: str) -> str:
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return json.dumps({"error": "arguments were not valid JSON"})
    handlers = {
        "search_docs": lambda: session.search_docs(str(args.get("query", ""))),
        "search_code": lambda: session.search_code(str(args.get("query", ""))),
        "read_code": lambda: session.read_code(str(args.get("id", ""))),
        "find_tables": lambda: session.find_tables(str(args.get("query", ""))),
        "describe_table": lambda: session.describe_table(str(args.get("table", ""))),
        "sql_examples": lambda: session.sql_examples([str(t) for t in args.get("tables") or []]),
        "run_query": lambda: session.run_query(str(args.get("sql", "")), str(args.get("purpose", ""))),
    }
    handler = handlers.get(name)
    if handler is None:
        return json.dumps({"error": f"unknown tool {name}"})
    try:
        result = handler()
    except Exception as error:  # noqa: BLE001 - a tool failure is reported to the AI, not raised
        result = {"error": f"{name} failed: {str(error).splitlines()[0][:200]}"}
    session.steps.append(describe_step(session, name, args, result))
    return json.dumps(result, default=str)


def describe_step(session: Session, name: str, args: dict, result: dict) -> dict:
    """A one-line, human-readable record of a tool call. Never includes data values."""
    found = len(result.get("results") or []) if isinstance(result, dict) else 0
    if name in ("search_docs", "search_code", "find_tables"):
        what = {"search_docs": "Searched the docs", "search_code": "Searched the code",
                "find_tables": "Looked for tables"}[name]
        detail = f"{what} for “{str(args.get('query', ''))[:80]}” — {found} found"
    elif name == "read_code":
        source = session.sources.get(str(args.get("id", "")))
        detail = f"Read {source.title}" if source else "Read code"
    elif name == "describe_table":
        detail = f"Read the columns of table {str(args.get('table', ''))[:60]}"
    elif name == "sql_examples":
        detail = f"Looked at how the code queries {', '.join(map(str, args.get('tables') or []))[:80]} — {found} found"
    elif name == "run_query":
        status = result.get("status") if isinstance(result, dict) else "?"
        tables = ", ".join(session.queries.get(result.get("id"), {}).get("tables", [])) if isinstance(
            result, dict) and result.get("id") else ""
        detail = f"Ran a read-only query{(' on ' + tables) if tables else ''} — {status}"
    else:
        detail = name
    if isinstance(result, dict) and result.get("error"):
        detail += " (error)"
    return {"tool": name, "detail": detail}
