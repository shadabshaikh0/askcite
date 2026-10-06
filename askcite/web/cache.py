"""Answer cache for the public demo: repeated questions are instant and use no AI quota.

Only used with --public-demo, whose data is fake; the normal product never stores answers with data values.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict

import psycopg

from askcite.brain import Answer
from askcite.tools import Source


def question_key(question: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", question.lower())).strip()


def cached_answer(store: psycopg.Connection, question: str, max_age_hours: int) -> Answer | None:
    row = store.execute("select answer from answer_cache where question_key = %s and created_at > now() - "
                        "make_interval(hours => %s)", (question_key(question), max_age_hours)).fetchone()
    if row is None:
        return None
    data = dict(row["answer"])
    data["sources"] = [Source(**s) for s in data.get("sources") or []]
    return Answer(**data)


def store_answer(store: psycopg.Connection, question: str, answer: Answer) -> None:
    if answer.status != "answered":
        return
    store.execute("insert into answer_cache (question_key, question, answer) values (%s, %s, %s) "
                  "on conflict (question_key) do update set answer = excluded.answer, created_at = now()",
                  (question_key(question), question, json.dumps(asdict(answer), default=str)))
