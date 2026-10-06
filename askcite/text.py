"""Small text helpers for search."""

from __future__ import annotations

import re

_STOP = {
    "a", "an", "the", "of", "in", "on", "for", "to", "and", "or", "is", "are", "was", "were", "be", "been", "by",
    "with", "from", "at", "as", "it", "its", "this", "that", "these", "those", "what", "which", "who", "whom",
    "how", "many", "much", "when", "where", "why", "do", "does", "did", "can", "could", "should", "would", "will",
    "i", "we", "you", "our", "my", "me", "us", "there", "their", "they", "about", "any", "all", "some", "please",
    "tell", "show", "give", "get", "happens", "happen", "whats", "work", "works",
}


def split_identifiers(text: str) -> str:
    """'processIsinTransactionsForDate' -> 'process Isin Transactions For Date'; snake_case -> words."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    return text.replace("_", " ")


def query_terms(question: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9]+", split_identifiers(question).lower())
    seen, terms = set(), []
    for word in words:
        if word in _STOP or len(word) < 2 or word in seen:
            continue
        seen.add(word)
        terms.append(word)
    return terms


def or_tsquery(question: str) -> str | None:
    """A to_tsquery() string that matches any of the meaningful words (ranked by how many match)."""
    terms = query_terms(question)
    return " | ".join(f"'{t}'" for t in terms) if terms else None
