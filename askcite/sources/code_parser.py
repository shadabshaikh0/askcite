"""Split Kotlin/Java source files into searchable pieces, and find the SQL written in the code.

- Every function/method becomes a chunk with its exact line range.
- Every class/object/interface becomes a short chunk (its header: name, constructor, fields).
- SQL strings are rebuilt from the pieces developers write them in
  (\"\"\"...\"\"\".trimIndent(), "select " + "...", $variables) and then read with sqlglot
  to learn which tables they touch. These become "SQL examples" that help the AI write
  new queries the way your team does.
"""

from __future__ import annotations

import logging
import re
import textwrap
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from tree_sitter_language_pack import get_parser

logging.getLogger("sqlglot").setLevel(logging.ERROR)

LANGUAGES = {".kt": "kotlin", ".kts": "kotlin", ".java": "java"}

_CLASS_NODES = {
    "kotlin": {"class_declaration", "object_declaration", "companion_object"},
    "java": {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"},
}
_FUNCTION_NODES = {
    "kotlin": {"function_declaration"},
    "java": {"method_declaration", "constructor_declaration"},
}
_SQL_START = re.compile(r"^\s*(\(\s*)?(select|insert|update|delete|with)\b", re.IGNORECASE)
_SQL_BODY = re.compile(r"\b(from|into|set|join)\b", re.IGNORECASE)
_MAX_CLASS_HEADER_LINES = 60


@dataclass
class CodeChunk:
    path: str
    symbol: str
    kind: str  # function | class
    start_line: int
    end_line: int
    content: str


@dataclass
class SqlExample:
    path: str
    symbol: str
    start_line: int
    end_line: int
    sql_text: str
    operation: str
    tables: list[str] = field(default_factory=list)
    parsed: bool = True


@dataclass
class ParsedFile:
    chunks: list[CodeChunk] = field(default_factory=list)
    sql_examples: list[SqlExample] = field(default_factory=list)


def _name_of(node, language: str) -> str:
    if node.type == "companion_object":
        return "Companion"
    for child in node.children:
        if child.type in ("type_identifier", "simple_identifier", "identifier"):
            return child.text.decode()
    return "<anonymous>"


class _Folder:
    """Rebuild string values from literal pieces inside one function."""

    def __init__(self, language: str, function_node, source: bytes):
        self.language = language
        self.source = source
        self.locals: dict[str, object] = {}
        self._collect_locals(function_node)

    def _collect_locals(self, node) -> None:
        for child in _walk(node):
            if self.language == "kotlin" and child.type == "property_declaration":
                name_node = next((c for c in child.children if c.type == "variable_declaration"), None)
                value = child.children[-1] if child.children and child.children[-2].type == "=" else None
                if name_node is not None and value is not None:
                    self.locals[name_node.text.decode()] = value
            elif self.language == "java" and child.type == "variable_declarator":
                name_node = child.child_by_field_name("name")
                value = child.child_by_field_name("value")
                if name_node is not None and value is not None:
                    self.locals[name_node.text.decode()] = value

    def fold(self, node, depth: int = 0) -> str | None:
        """Return the string value of an expression, with unknown parts as /*name*/ markers."""
        if depth > 8:
            return None
        kind = node.type
        if kind in ("string_literal", "text_block", "multiline_string_literal"):
            return self._literal(node)
        if kind in ("additive_expression", "binary_expression"):
            parts = [c for c in node.children if c.type != "+"]
            operators = [c for c in node.children if c.type == "+"]
            if len(parts) != 2 or not operators:
                return None
            left, right = self.fold(parts[0], depth + 1), self.fold(parts[1], depth + 1)
            if left is None and right is None:
                return None
            left = left if left is not None else _marker(parts[0])
            return left + (right if right is not None else _marker(parts[1]))
        if kind == "parenthesized_expression":
            inner = [c for c in node.children if c.type not in ("(", ")")]
            return self.fold(inner[0], depth + 1) if inner else None
        if kind == "call_expression" and self.language == "kotlin":
            callee = node.children[0]
            if callee.type == "navigation_expression":
                target, suffix = callee.children[0], callee.children[-1].text.decode()
                if suffix in (".trimIndent", ".trimMargin", ".trim"):
                    value = self.fold(target, depth + 1)
                    if value is None:
                        return None
                    return textwrap.dedent(value).strip() if suffix != ".trimMargin" else _trim_margin(value)
            return None
        if kind in ("simple_identifier", "identifier"):
            value_node = self.locals.get(node.text.decode())
            if value_node is not None and value_node is not node:
                return self.fold(value_node, depth + 1)
            return None
        return None

    def _literal(self, node) -> str:
        pieces = []
        for child in node.children:
            ctype = child.type
            if ctype in ("string_content", "string_fragment", "multiline_string_fragment", "escape_sequence"):
                text = child.text.decode()
                pieces.append(text.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
                              if ctype == "escape_sequence" else text)
            elif ctype in ("interpolated_identifier", "interpolated_expression"):
                pieces.append(f"/*{child.text.decode()}*/")
        return "".join(pieces)


def _marker(node) -> str:
    return f"/*{node.text.decode()[:40]}*/"


def _trim_margin(value: str) -> str:
    return "\n".join(re.sub(r"^\s*\|", "", line) for line in value.splitlines()).strip()


def _walk(node):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def _outermost_string_expression(node, language: str):
    """From a string literal, climb to the whole expression (concatenation / .trimIndent())."""
    current = node
    while current.parent is not None:
        parent = current.parent
        if parent.type in ("additive_expression", "binary_expression", "parenthesized_expression"):
            current = parent
            continue
        if language == "kotlin" and parent.type == "navigation_expression" and parent.parent is not None \
                and parent.parent.type == "call_expression":
            suffix = parent.children[-1].text.decode()
            if suffix in (".trimIndent", ".trimMargin", ".trim"):
                current = parent.parent
                continue
        break
    return current


def _numbered_params(text: str) -> str:
    """JDBC `?` placeholders -> `$1` so `?::bigint` parses (quotes are left alone)."""
    out, in_quote = [], False
    for ch in text:
        if ch == "'":
            in_quote = not in_quote
        out.append("$1" if ch == "?" and not in_quote else ch)
    return "".join(out)


def analyze_sql(text: str) -> tuple[str, list[str], bool]:
    """Return (operation, tables, parsed_ok) for one SQL string taken from code."""
    numbered = _numbered_params(text)
    candidates = [text, numbered, re.sub(r"/\*[^*]*\*/", "", numbered),
                  re.sub(r"/\*([^*]*)\*/", "dyn_value", numbered)]
    for candidate in candidates:
        try:
            tree = sqlglot.parse_one(candidate, dialect="postgres")
        except Exception:  # noqa: BLE001 - try the next variant
            continue
        if tree is None or isinstance(tree, exp.Command):
            continue
        ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        tables = sorted({t.name.lower() for t in tree.find_all(exp.Table) if t.name and t.name.lower() not in ctes})
        operation = {exp.Select: "select", exp.Union: "select", exp.Insert: "insert", exp.Update: "update",
                     exp.Delete: "delete"}.get(type(tree), "other")
        return operation, tables, True
    tables = sorted({m.lower() for m in re.findall(r"\b(?:from|join|into|update)\s+([a-zA-Z_][\w.]*)", text, re.I)
                     if m.lower() not in ("select", "set", "where", "lateral", "unnest")})
    operation = (_SQL_START.match(text).group(2).lower() if _SQL_START.match(text) else "other")
    return operation, tables, False


def parse_source(path: str, text: str) -> ParsedFile:
    suffix = "." + path.rsplit(".", 1)[-1] if "." in path else ""
    language = LANGUAGES.get(suffix)
    result = ParsedFile()
    if language is None:
        return result
    source = text.encode()
    tree = get_parser(language).parse(source)
    lines = text.splitlines()

    def visit(node, owners: list[str]):
        for child in node.children:
            if child.type in _CLASS_NODES[language]:
                name = _name_of(child, language)
                qualified = ".".join(owners + [name])
                start, end = child.start_point[0] + 1, child.end_point[0] + 1
                first_function = next((n for n in _walk(child)
                                       if n is not child and n.type in _FUNCTION_NODES[language]), None)
                header_end = min(end, (first_function.start_point[0] if first_function else end),
                                 start + _MAX_CLASS_HEADER_LINES - 1)
                result.chunks.append(CodeChunk(path, qualified, "class", start, header_end,
                                               "\n".join(lines[start - 1: header_end])))
                visit(child, owners + [name])
            elif child.type in _FUNCTION_NODES[language]:
                name = _name_of(child, language) if language == "kotlin" else (
                    child.child_by_field_name("name").text.decode() if child.child_by_field_name("name") else "<init>")
                qualified = ".".join(owners + [name])
                start, end = child.start_point[0] + 1, child.end_point[0] + 1
                body = "\n".join(lines[start - 1: end])
                result.chunks.append(CodeChunk(path, qualified, "function", start, end, body))
                _collect_sql(child, language, source, path, qualified, result)
                visit(child, owners + [name])  # local/nested functions
            else:
                visit(child, owners)

    visit(tree.root_node, [])
    return result


def _collect_sql(function_node, language: str, source: bytes, path: str, symbol: str, result: ParsedFile) -> None:
    folder = _Folder(language, function_node, source)
    seen_spans: set[tuple[int, int]] = set()
    for node in _walk(function_node):
        if node.type not in ("string_literal", "text_block", "multiline_string_literal"):
            continue
        expression = _outermost_string_expression(node, language)
        span = (expression.start_byte, expression.end_byte)
        if span in seen_spans:
            continue
        seen_spans.add(span)
        value = folder.fold(expression)
        if not value or not _SQL_START.match(value) or not _SQL_BODY.search(value):
            continue
        sql_text = textwrap.dedent(value).strip()
        operation, tables, parsed = analyze_sql(sql_text)
        if not tables:
            continue
        result.sql_examples.append(SqlExample(
            path=path, symbol=symbol, start_line=expression.start_point[0] + 1, end_line=expression.end_point[0] + 1,
            sql_text=sql_text, operation=operation, tables=tables, parsed=parsed,
        ))
