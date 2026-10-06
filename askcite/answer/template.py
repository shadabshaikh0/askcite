"""Fill an answer template with real query results — locally, without any AI.

The cloud AI writes answers with blanks such as:
    "{{Q1}} orders were settled yesterday."
Askcite fills them from the query results it ran itself:
    {{Q1}}              the single value of a 1x1 result
    {{Q1.count}}        number of rows returned
    {{Q1.<column>}}     that column in the first row
    {{Q1.list:<column>}} that column from every row, comma separated (max 20)
    {{Q1.table}}        the result as a small table
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from askcite.data.runner import QueryResult

_PLACEHOLDER = re.compile(r"\{\{\s*(Q\d+)(?:\.(count|table|list:[\w]+|[\w]+))?\s*\}\}")
_MAX_TABLE_ROWS = 15


def format_value(value: Any, tz: str = "UTC") -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, (float, Decimal)):
        number = float(value)
        return f"{number:,.0f}" if number.is_integer() else f"{number:,.2f}"
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)  # stored values are UTC
        return moment.astimezone(ZoneInfo(tz)).strftime("%d %b %Y, %H:%M")
    if isinstance(value, date):
        return value.strftime("%d %b %Y")
    return str(value)


def render_table(result: QueryResult, tz: str = "UTC", max_rows: int = _MAX_TABLE_ROWS) -> str:
    rows = [[format_value(v, tz) for v in row] for row in result.rows[:max_rows]]
    header = result.columns
    widths = [max([len(h)] + [len(r[i]) for r in rows]) for i, h in enumerate(header)]
    lines = [" | ".join(h.ljust(widths[i]) for i, h in enumerate(header)),
             "-+-".join("-" * w for w in widths)]
    lines += [" | ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)) for r in rows]
    more = result.row_count - len(rows)
    if more > 0 or result.truncated:
        lines.append(f"... {more if more > 0 else 'more'} more rows")
    return "```\n" + "\n".join(lines) + "\n```"


def fill(template: str, results: dict[str, QueryResult], tz: str = "UTC") -> tuple[str, list[str]]:
    """Return (filled text, problems). Unknown blanks become '?' and are reported."""
    problems: list[str] = []

    def replace(match: re.Match) -> str:
        query_id, part = match.group(1), match.group(2)
        result = results.get(query_id)
        if result is None:
            problems.append(f"{query_id} has no result")
            return "?"
        if part is None:
            if result.row_count == 1 and len(result.columns) == 1:
                return format_value(result.rows[0][0], tz)
            if result.row_count == 0:
                return "0" if result.columns and "count" in result.columns[0].lower() else "none"
            return "\n" + render_table(result, tz) + "\n"
        if part == "count":
            return f"{result.row_count:,}" + ("+" if result.truncated else "")
        if part == "table":
            return "\n" + render_table(result, tz) + "\n" if result.row_count else "(no rows)"
        if part.startswith("list:"):
            column = part.split(":", 1)[1]
            if column not in result.columns:
                problems.append(f"{query_id} has no column {column}")
                return "?"
            index = result.columns.index(column)
            values = [format_value(r[index], tz) for r in result.rows[:20]]
            suffix = f" and {result.row_count - 20} more" if result.row_count > 20 else ""
            return ", ".join(values) + suffix if values else "none"
        if part in result.columns:
            if not result.rows:
                return "none"
            return format_value(result.rows[0][result.columns.index(part)], tz)
        problems.append(f"{query_id} has no column {part}")
        return "?"

    return _PLACEHOLDER.sub(replace, template), problems


def placeholders(template: str) -> list[str]:
    return [m.group(0) for m in _PLACEHOLDER.finditer(template)]
