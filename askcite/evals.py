"""Run a list of test questions and score the answers.

questions.yaml entries:
  - id: data-01
    question: How many orders were settled yesterday?
    answer_from: [data]                 # docs | code | data (informational)
    expected_tables: [orders]           # tables the query should use
    expected_sources: [PaymentService.kt]          # text expected in a source title or link
    expected_sql: select count(*) ...   # optional: the answer's query must return the same rows
    must_mention: [SETTLED]             # optional: words the answer should contain
    expect_refusal: false               # true for questions asking for personal data
"""

from __future__ import annotations

from pathlib import Path

import yaml

from askcite.brain import Brain
from askcite.tools import Asker


def _rows(runner, sql: str):
    result = runner.run(sql)
    return sorted(tuple(str(v) for v in row) for row in result.rows)


def run_eval(brain: Brain, questions_file: Path, only: list[str] | None = None) -> dict:
    cases = yaml.safe_load(Path(questions_file).read_text()) or []
    if only:
        cases = [c for c in cases if c["id"] in only]
    runner = brain.workspace.runner
    results = []
    for case in cases:
        answer = brain.ask(case["question"], Asker("eval", {"everyone", "data"}))
        source_text = " ".join(f"{s.title} {s.url or ''}" for s in answer.sources).lower()
        used_tables = {t for q in answer.queries.values() for t in q["tables"]}
        row = {"id": case["id"], "status": answer.status, "confidence": answer.confidence,
               "answer": answer.text, "sources": [s.title for s in answer.sources],
               "queries": answer.queries, "tool_calls": answer.tool_calls, "seconds": answer.duration_ms / 1000}
        if case.get("expected_sources"):
            row["source_match"] = any(e.lower() in source_text for e in case["expected_sources"])
        if case.get("expected_tables"):
            row["tables_match"] = set(t.lower() for t in case["expected_tables"]) <= used_tables
        if case.get("must_mention"):
            row["mentions"] = all(m.lower() in answer.text.lower() for m in case["must_mention"])
        if case.get("expected_sql") and runner is not None:
            ok_queries = [q["sql"] for q in answer.queries.values() if q["status"] == "ok"]
            try:
                expected = _rows(runner, case["expected_sql"])
                row["result_match"] = any(_rows(runner, sql) == expected for sql in ok_queries)
            except Exception as error:  # noqa: BLE001
                row["result_match"] = False
                row["result_error"] = str(error).splitlines()[0]
        if case.get("expect_refusal"):
            row["refused_correctly"] = not any(q["status"] == "ok" for q in answer.queries.values()) or \
                answer.status != "answered"
        results.append(row)

    def rate(key: str) -> float:
        scored = [r[key] for r in results if key in r]
        return sum(scored) / len(scored) if scored else 1.0

    blocked = 0
    store = brain.workspace.store
    if store is not None:
        blocked = store.execute("select count(*) as n from query_audit where slack_user = 'eval' and status = "
                                "'blocked'").fetchone()["n"]
    summary = {"questions": len(results), "answered": rate_answered(results), "source_match": rate("source_match"),
               "tables_match": rate("tables_match"), "result_match": rate("result_match"),
               "mentions": rate("mentions"), "refused_correctly": rate("refused_correctly"), "blocked": blocked,
               "avg_seconds": round(sum(r["seconds"] for r in results) / len(results), 1) if results else 0,
               "avg_tool_calls": round(sum(r["tool_calls"] for r in results) / len(results), 1) if results else 0}
    return {"summary": summary, "results": results}


def to_markdown(report: dict, model: str) -> str:
    """A results table for README / docs/benchmarks.md."""
    s = report["summary"]
    lines = [
        f"### {model}",
        "",
        "| Questions | Answered | Right source cited | Right tables | Same numbers as expected | "
        "Mentions key facts | Refused personal data | Avg time | Avg tool calls |",
        "|---|---|---|---|---|---|---|---|---|",
        f"| {s['questions']} | {s['answered']:.0%} | {s['source_match']:.0%} | {s['tables_match']:.0%} | "
        f"{s['result_match']:.0%} | {s['mentions']:.0%} | {s['refused_correctly']:.0%} | {s['avg_seconds']} s | "
        f"{s['avg_tool_calls']} |",
        "",
        "| Question | Status | Checks |",
        "|---|---|---|",
    ]
    for row in report["results"]:
        checks = [f"{key.replace('_', ' ')}: {'✓' if row[key] else '✗'}" for key in
                  ("source_match", "tables_match", "result_match", "mentions", "refused_correctly") if key in row]
        lines.append(f"| {row['id']} | {row['status']} | {', '.join(checks) or '—'} |")
    return "\n".join(lines) + "\n"


def rate_answered(results: list[dict]) -> float:
    return sum(r["status"] == "answered" for r in results) / len(results) if results else 0.0
