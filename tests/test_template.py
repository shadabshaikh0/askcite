from datetime import datetime
from decimal import Decimal

from askcite.answer.template import fill, format_value
from askcite.data.runner import QueryResult


def result(columns, rows, truncated=False):
    return QueryResult(columns=columns, rows=rows, truncated=truncated, duration_ms=1)


def test_single_value_and_count():
    text, problems = fill("{{Q1}} orders were settled; {{Q2.count}} cities.",
                          {"Q1": result(["count"], [(1234,)]), "Q2": result(["city"], [("A",), ("B",)])})
    assert text == "1,234 orders were settled; 2 cities." and problems == []


def test_columns_lists_and_tables():
    rows = [("PAID", 3, Decimal("10.5")), ("SETTLED", 7, Decimal("99"))]
    data = {"Q1": result(["status", "orders", "amount"], rows)}
    assert fill("Top: {{Q1.status}}", data)[0] == "Top: PAID"
    assert fill("{{Q1.list:status}}", data)[0] == "PAID, SETTLED"
    table = fill("{{Q1.table}}", data)[0]
    assert "SETTLED" in table and "10.50" in table and table.strip().startswith("```")


def test_unknown_placeholders_are_reported():
    text, problems = fill("{{Q9}} and {{Q1.nope}}", {"Q1": result(["a"], [(1,)])})
    assert text == "? and ?" and len(problems) == 2


def test_utc_timestamps_are_shown_in_local_time():
    assert format_value(datetime(2026, 9, 28, 18, 30), "Asia/Kolkata") == "29 Sep 2026, 00:00"
