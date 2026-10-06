"""Search Askcite's index: code functions, SQL examples from code, and Notion sections."""

from __future__ import annotations

import psycopg

from askcite.text import or_tsquery, query_terms


def search_code(store: psycopg.Connection, question: str, limit: int = 8, repo: str | None = None) -> list[dict]:
    tsquery = or_tsquery(question)
    if not tsquery:
        return []
    identifier = max(question.split(), key=len) if question.split() else ""
    return store.execute(
        """
        select id, repo, commit_sha, path, symbol, kind, start_line, end_line,
               left(content, 1500) as preview,
               ts_rank_cd(search, to_tsquery('english', %(q)s), 32) + similarity(symbol, %(ident)s) as score
        from code_chunk
        where (search @@ to_tsquery('english', %(q)s) or symbol %% %(ident)s)
          and (%(repo)s::text is null or repo = %(repo)s)
        order by score desc
        limit %(limit)s
        """,
        {"q": tsquery, "ident": identifier, "repo": repo, "limit": limit},
    ).fetchall()


def read_code(store: psycopg.Connection, chunk_id: int) -> dict | None:
    return store.execute("select id, repo, commit_sha, path, symbol, kind, start_line, end_line, content "
                         "from code_chunk where id = %s", (chunk_id,)).fetchone()


def find_symbol(store: psycopg.Connection, name: str, limit: int = 5) -> list[dict]:
    return store.execute(
        "select id, repo, commit_sha, path, symbol, kind, start_line, end_line from code_chunk "
        "where symbol ilike %(pattern)s or symbol %% %(name)s "
        "order by (symbol ilike %(pattern)s) desc, similarity(symbol, %(name)s) desc limit %(limit)s",
        {"pattern": f"%{name}%", "name": name, "limit": limit},
    ).fetchall()


def sql_examples(store: psycopg.Connection, tables: list[str], question: str = "", limit: int = 5) -> list[dict]:
    """Real queries from the codebase that use these tables — the best guide to writing new SQL."""
    tsquery = or_tsquery(question) if question else None
    return store.execute(
        """
        select id, repo, commit_sha, path, symbol, start_line, end_line, sql_text, tables, operation
        from sql_example
        where tables && %(tables)s and parsed and operation = 'select'
        order by cardinality(array(select unnest(tables) intersect select unnest(%(tables)s::text[]))) desc,
                 cardinality(tables),
                 case when %(q)s::text is null then 0 else ts_rank_cd(search, to_tsquery('english', %(q)s)) end desc,
                 length(sql_text)
        limit %(limit)s
        """,
        {"tables": [t.lower() for t in tables], "q": tsquery, "limit": limit},
    ).fetchall()


def search_docs(store: psycopg.Connection, question: str, limit: int = 6) -> list[dict]:
    tsquery = or_tsquery(question)
    if not tsquery:
        return []
    return store.execute(
        """
        select id, page_id, block_id, page_title, heading_path, url, left(content, 2000) as content, last_edited,
               ts_rank_cd(search, to_tsquery('english', %(q)s), 32) as score
        from doc_section
        where search @@ to_tsquery('english', %(q)s)
        order by score desc
        limit %(limit)s
        """,
        {"q": tsquery, "limit": limit},
    ).fetchall()


def matched_terms(question: str) -> list[str]:
    return query_terms(question)


_VALUE_EQ = r"(?:\b\w+\.)?\b{col}\s*(?:=|!=|<>)\s*'([^'%]{{1,40}})'"
_VALUE_IN = r"(?:\b\w+\.)?\b{col}\s+(?:not\s+)?in\s*\(([^)]{{1,400}})\)"


def values_seen_in_code(store: psycopg.Connection, table: str, columns: list[str],
                        limit: int = 12) -> dict[str, list[str]]:
    """Literal values the team's own SQL compares these columns with, e.g. status = 'SETTLED'."""
    import re

    rows = store.execute("select sql_text from sql_example where %s = any(tables) limit 400",
                         (table.lower(),)).fetchall()
    found: dict[str, dict[str, int]] = {}
    for row in rows:
        text = row["sql_text"]
        for column in columns:
            if column.lower() not in text.lower():
                continue
            values = re.findall(_VALUE_EQ.format(col=re.escape(column)), text, re.IGNORECASE)
            for group in re.findall(_VALUE_IN.format(col=re.escape(column)), text, re.IGNORECASE):
                values += re.findall(r"'([^'%]{1,40})'", group)
            for value in values:
                if value.startswith("/*"):
                    continue  # a Kotlin/Java constant we could not resolve, not a real value
                counts = found.setdefault(column, {})
                counts[value] = counts.get(value, 0) + 1
    return {column: [v for v, _ in sorted(counts.items(), key=lambda item: -item[1])[:limit]]
            for column, counts in found.items()}
