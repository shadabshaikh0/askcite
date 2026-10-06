"""The schema catalog: table and column names, types, keys and indexes.

This is metadata only. Askcite never stores rows from your database.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from askcite.config import Glossary


class Column(BaseModel):
    name: str
    type: str = "text"
    nullable: bool = True
    default: str | None = None
    comment: str | None = None
    allowed_values: list[str] | None = None  # from CHECK (col IN (...)) constraints


class ForeignKey(BaseModel):
    columns: list[str]
    ref_table: str
    ref_columns: list[str]


class Index(BaseModel):
    name: str
    columns: list[str]
    unique: bool = False
    definition: str | None = None


class Table(BaseModel):
    name: str
    schema_name: str = "public"
    columns: list[Column] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)
    foreign_keys: list[ForeignKey] = Field(default_factory=list)
    indexes: list[Index] = Field(default_factory=list)
    comment: str | None = None
    is_view: bool = False
    estimated_rows: int | None = None

    def column(self, name: str) -> Column | None:
        lowered = name.lower()
        return next((c for c in self.columns if c.name.lower() == lowered), None)


class Catalog(BaseModel):
    source: str = "db"
    tables: dict[str, Table] = Field(default_factory=dict)  # key: lower-case table name
    problems: list[str] = Field(default_factory=list)  # statements we could not understand (schema files)

    def table(self, name: str) -> Table | None:
        return self.tables.get(name.lower().split(".")[-1].strip('"'))

    def add_table(self, table: Table) -> None:
        self.tables[table.name.lower()] = table

    def find_tables(self, query: str, glossary: Glossary | None = None, limit: int = 10) -> list[tuple[Table, float]]:
        """Rank tables for a plain-English query using names, columns and glossary text."""
        terms = _terms(query)
        if not terms:
            return []
        scored: list[tuple[Table, float]] = []
        for table in self.tables.values():
            note = glossary.tables.get(table.name) if glossary else None
            name_terms = set(_terms(table.name))
            column_terms = {t for c in table.columns for t in _terms(c.name)}
            text_terms = set(_terms(" ".join(filter(None, [table.comment, note.description if note else None]))))
            if note:
                text_terms |= set(_terms(" ".join(note.columns.values())))
            score = 0.0
            for term in terms:
                if term in name_terms:
                    score += 3.0
                elif any(term in n or n in term for n in name_terms if len(n) > 3):
                    score += 1.5
                if term in text_terms:
                    score += 2.0
                if term in column_terms:
                    score += 0.5
            if score > 0:
                scored.append((table, score))
        scored.sort(key=lambda item: (-item[1], item[0].name))
        return scored[:limit]


_STOP = {"the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "how", "many", "much", "what", "which",
         "is", "are", "was", "were", "by", "with", "from", "yesterday", "today", "last", "week", "month", "all"}


def _terms(text: str) -> list[str]:
    # split snake_case and camelCase, lower-case, drop stop words, and fold simple plurals
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "")
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9]+", text.replace("_", " ").lower())
    out = []
    for word in words:
        if word in _STOP:
            continue
        if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        out.append(word)
    return out
