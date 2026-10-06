"""Read a schema from a SQL file (no data needed).

Works with a clean `pg_dump --schema-only` file and also with hand-maintained
"migration log" schema files, which mix CREATE/ALTER
statements, miss some semicolons, and contain functions, triggers and INSERTs.

We replay the DDL in order into a Catalog: CREATE TABLE, ALTER TABLE
(add/drop/rename column, change type, add keys/checks), CREATE INDEX,
CREATE VIEW, DROP TABLE and COMMENT ON. Everything else is ignored.
Statements we cannot understand are listed in `catalog.problems`.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import sqlglot
from sqlglot import exp

from askcite.schema.catalog import Catalog, Column, ForeignKey, Index, Table

logging.getLogger("sqlglot").setLevel(logging.ERROR)  # we report unparsed statements ourselves

_OBJECT = r"(table|index|unique|view|materialized|sequence|type|function|procedure|trigger|extension|schema|or)"
_STATEMENT_START = re.compile(
    rf"^\s*(create\s+{_OBJECT}|alter\s+(table|index|view|sequence|type|function|role|user|\w+\s+add\b)|"
    rf"drop\s+({_OBJECT}|concurrently)|insert\s+into|update\s+\w+\s+set|delete\s+from|comment\s+on|"
    r"grant|revoke|truncate|do\s|begin\s*;|commit\s*;|refresh|vacuum|analyze|reindex)\b",
    re.IGNORECASE,
)
# CREATE/ALTER TABLE never legitimately appear inside parentheses outside function bodies,
# so they also end a statement whose parenthesis was never closed (a common hand-written slip).
_HARD_START = re.compile(r"^\s*(create|alter)\s+table\b", re.IGNORECASE)
_DDL_OF_INTEREST = re.compile(r"^\s*(create|alter|drop|comment)\b", re.IGNORECASE)
_SKIP_CREATE = re.compile(
    r"^\s*create\s+(or\s+replace\s+)?(function|procedure|trigger|type|sequence|extension|schema|role|user|"
    r"policy|rule|aggregate|domain|publication|subscription|event)\b",
    re.IGNORECASE,
)
_DOLLAR_TAG = re.compile(r"\$[A-Za-z_]*\$")


def split_statements(sql: str) -> list[str]:
    """Split SQL text into statements.

    Ends a statement at `;`, and also at a new line that starts a new statement
    keyword while we are at parenthesis depth 0 (for files with missing semicolons).
    Respects quotes, dollar-quoted bodies and comments.
    """
    statements: list[str] = []
    buf: list[str] = []
    depth = 0
    in_quote = False
    dollar_tag: str | None = None
    i = 0
    n = len(sql)

    def flush():
        text = "".join(buf).strip()
        if text:
            statements.append(text)
        buf.clear()

    at_line_start = True
    while i < n:
        ch = sql[i]
        if dollar_tag:
            if sql.startswith(dollar_tag, i):
                buf.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
                continue
            buf.append(ch)
            i += 1
            continue
        if in_quote:
            buf.append(ch)
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                in_quote = False
            i += 1
            continue
        if at_line_start and "".join(buf).strip():
            line_end = sql.find("\n", i)
            line = sql[i: line_end if line_end != -1 else n]
            if (depth == 0 and _STATEMENT_START.match(line)) or _HARD_START.match(line):
                flush()
                depth = 0
        at_line_start = False
        if ch == "-" and sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end == -1 else end  # drop the comment, keep the newline
            continue
        if ch == "/" and sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if ch == "'":
            in_quote = True
        elif ch == "$":
            match = _DOLLAR_TAG.match(sql, i)
            if match:
                dollar_tag = match.group(0)
                buf.append(dollar_tag)
                i += len(dollar_tag)
                continue
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == ";" and depth == 0:
            flush()
            i += 1
            continue
        elif ch == "\n":
            at_line_start = True
        buf.append(ch)
        i += 1
    flush()
    return statements


def _repair(statement: str) -> str:
    fixed = _cut_after_column_list(statement)
    fixed = re.sub(r",\s*\)", ")", fixed)  # trailing comma before a closing parenthesis
    fixed = re.sub(r"\bcreate\s+index\s+concurrently\b", "create index", fixed, flags=re.IGNORECASE)
    fixed = re.sub(r"\bdefault\s*\(\s*now\s*\)", "default now()", fixed, flags=re.IGNORECASE)
    fixed = re.sub(r"\bcheck\s+(\w+\s+in\s*\([^)]*\))", r"check (\1)", fixed, flags=re.IGNORECASE)
    return fixed


def _cut_after_column_list(statement: str) -> str:
    """Drop stray text after the closing parenthesis of CREATE TABLE (...) — e.g. a pasted URL."""
    if not re.match(r"^\s*create\s+table\b", statement, re.IGNORECASE):
        return statement
    start = statement.find("(")
    if start == -1:
        return statement
    depth = 0
    for index in range(start, len(statement)):
        if statement[index] == "(":
            depth += 1
        elif statement[index] == ")":
            depth -= 1
            if depth == 0:
                return statement[: index + 1]
    return statement + ")"  # the closing parenthesis was never written


def _top_level_split(text: str) -> list[str]:
    parts, depth, current = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if "".join(current).strip():
        parts.append("".join(current).strip())
    return parts


_COLUMN_ITEM = re.compile(r'^"?(\w+)"?\s+([a-zA-Z][\w ]*?(?:\([\d, ]+\))?(?:\[\])?)(?=\s|$|,)(.*)$', re.DOTALL)
_NOT_A_COLUMN = {"primary", "unique", "constraint", "foreign", "check", "exclude", "like"}


def _lenient_column(item: str, table: Table) -> None:
    """Best-effort column from a hand-written column definition that sqlglot rejected."""
    match = _COLUMN_ITEM.match(item.strip())
    if not match or match.group(1).lower() in _NOT_A_COLUMN:
        pk = re.match(r"^primary\s+key\s*\(([^)]*)\)", item.strip(), re.IGNORECASE)
        if pk:
            table.primary_key = [c.strip().strip('"').lower() for c in pk.group(1).split(",")]
        return
    name, col_type, rest = match.group(1).lower(), match.group(2).strip().lower(), match.group(3)
    col_type = re.split(r"\s+(not|null|default|primary|references|unique|check|constraint)\b", col_type)[0]
    column = Column(name=name, type=col_type, nullable=not re.search(r"\bnot\s+null\b", rest, re.IGNORECASE))
    values = re.search(r"\bcheck\s*\(?\s*\w+\s+in\s*\(([^)]*)\)", rest, re.IGNORECASE)
    if values:
        column.allowed_values = re.findall(r"'([^']*)'", values.group(1)) or None
    if re.search(r"\bprimary\s+key\b", rest, re.IGNORECASE):
        table.primary_key = [name]
    ref = re.search(r"\breferences\s+\"?(\w+)\"?\s*(?:\(\s*\"?(\w+)\"?\s*\))?", rest, re.IGNORECASE)
    if ref:
        table.foreign_keys.append(ForeignKey(columns=[name], ref_table=ref.group(1).lower(),
                                             ref_columns=[(ref.group(2) or name).lower()]))
    existing = table.column(name)
    if existing is not None:
        table.columns.remove(existing)
    table.columns.append(column)


def _lenient_statement(statement: str, catalog: Catalog) -> bool:
    """Regex fallback for CREATE TABLE / ALTER TABLE that the SQL parser rejected. Returns True if applied."""
    create = re.match(r"^\s*create\s+table\s+(?:if\s+not\s+exists\s+)?\"?(?:\w+\.)?(\w+)\"?\s*\(", statement,
                      re.IGNORECASE)
    if create:
        body = _cut_after_column_list(statement)
        body = body[body.find("(") + 1: -1]
        table = Table(name=create.group(1).lower())
        for item in _top_level_split(body):
            _lenient_column(item, table)
        if table.columns:
            catalog.add_table(table)
            return True
        return False
    alter = re.match(r"^\s*alter\s+(?:table\s+)?(?:if\s+exists\s+)?(?:only\s+)?\"?(?:\w+\.)?(\w+)\"?\s+(.*)$",
                     statement, re.IGNORECASE | re.DOTALL)
    if not alter:
        return False
    table = catalog.table(alter.group(1))
    if table is None:
        return False
    applied = False
    for action in _top_level_split(alter.group(2)):
        action = re.sub(r"--[^\n]*", "", action).strip()
        add = re.match(r"^add\s+(?:column\s+)?(?:if\s+not\s+exists\s+)?"
                       r"(?!constraint\b|primary\b|unique\b|foreign\b|check\b)(.*)$", action, re.IGNORECASE | re.DOTALL)
        drop = re.match(r"^drop\s+(?:column\s+)?(?:if\s+exists\s+)?\"?(\w+)\"?\s*$", action, re.IGNORECASE)
        rename = re.match(r"^rename\s+(?:column\s+)?\"?(\w+)\"?\s+to\s+\"?(\w+)\"?", action, re.IGNORECASE)
        retype = re.match(r"^alter\s+(?:column\s+)?\"?(\w+)\"?\s+(?:set\s+data\s+)?type\s+([\w ()]+)", action,
                          re.IGNORECASE)
        if add:
            _lenient_column(add.group(1), table)
            applied = True
        elif drop and drop.group(1).lower() not in ("constraint", "not", "default"):
            existing = table.column(drop.group(1))
            if existing:
                table.columns.remove(existing)
                applied = True
        elif rename:
            existing = table.column(rename.group(1))
            if existing:
                existing.name = rename.group(2).lower()
                applied = True
        elif retype:
            existing = table.column(retype.group(1))
            if existing:
                existing.type = retype.group(2).strip().lower()
                applied = True
        else:
            # nothing schema-relevant (e.g. a constraint change)
            applied = applied or bool(re.match(r"^(alter|drop|add)\s", action, re.IGNORECASE))
    return applied


def _parse(statement: str) -> exp.Expression | None:
    for candidate in (statement, _repair(statement)):
        try:
            parsed = sqlglot.parse_one(candidate, dialect="postgres")
        except Exception:  # noqa: BLE001 - any parse failure means "try the next repair"
            continue
        if parsed is not None and not isinstance(parsed, exp.Command):
            return parsed
    return None


def _name(node: exp.Expression | None) -> str:
    if node is None:
        return ""
    if isinstance(node, exp.Table):
        return node.name.lower()
    if isinstance(node, (exp.Identifier, exp.Column)):
        return node.name.lower()
    return node.sql(dialect="postgres").lower()


def _literal_values(node: exp.Expression) -> tuple[str | None, list[str] | None]:
    """For CHECK (col IN ('A','B')) return ('col', ['A','B'])."""
    if isinstance(node, exp.In) and isinstance(node.this, exp.Column):
        values = [e.this for e in node.expressions if isinstance(e, exp.Literal)]
        if values and len(values) == len(node.expressions):
            return node.this.name.lower(), [str(v) for v in values]
    return None, None


class _Replayer:
    def __init__(self, catalog: Catalog):
        self.catalog = catalog

    def apply(self, parsed: exp.Expression) -> None:
        if isinstance(parsed, exp.Create):
            kind = (parsed.args.get("kind") or "").upper()
            if kind == "TABLE":
                self._create_table(parsed)
            elif kind == "INDEX":
                self._create_index(parsed)
            elif kind in ("VIEW", "MATERIALIZED VIEW") or parsed.args.get("materialized"):
                self._create_view(parsed)
        elif isinstance(parsed, exp.Alter) and (parsed.args.get("kind") or "").upper() == "TABLE":
            self._alter_table(parsed)
        elif isinstance(parsed, exp.Drop) and (parsed.args.get("kind") or "").upper() in ("TABLE", "VIEW"):
            table = parsed.this if isinstance(parsed.this, exp.Table) else parsed.find(exp.Table)
            self.catalog.tables.pop(_name(table), None)
        elif isinstance(parsed, exp.Comment):
            self._comment(parsed)

    def _table(self, name: str) -> Table:
        table = self.catalog.table(name)
        if table is None:
            table = Table(name=name)
            self.catalog.add_table(table)
        return table

    def _create_table(self, parsed: exp.Create) -> None:
        schema = parsed.this
        if not isinstance(schema, exp.Schema):
            return  # CREATE TABLE ... AS SELECT: skip
        name = _name(schema.this)
        if self.catalog.table(name) and parsed.args.get("exists"):
            return  # CREATE TABLE IF NOT EXISTS on an existing table: no-op
        table = Table(name=name)
        self.catalog.add_table(table)
        for item in schema.expressions:
            self._apply_table_element(table, item)

    def _apply_table_element(self, table: Table, item: exp.Expression) -> None:
        if isinstance(item, exp.ColumnDef):
            self._add_column(table, item)
        elif isinstance(item, exp.PrimaryKey):
            table.primary_key = [_name(e) for e in item.expressions]
        elif isinstance(item, exp.Constraint):
            for inner in item.expressions:
                self._apply_table_element(table, inner)
        elif isinstance(item, exp.ForeignKey):
            ref = item.args.get("reference")
            if ref is not None and isinstance(ref.this, exp.Schema):
                table.foreign_keys.append(ForeignKey(
                    columns=[_name(e) for e in item.expressions],
                    ref_table=_name(ref.this.this),
                    ref_columns=[_name(e) for e in ref.this.expressions],
                ))
        elif isinstance(item, exp.CheckColumnConstraint):
            col_name, values = _literal_values(item.this)
            column = table.column(col_name) if col_name else None
            if column is not None:
                column.allowed_values = values

    def _add_column(self, table: Table, coldef: exp.ColumnDef) -> None:
        name = coldef.name.lower()
        kind = coldef.args.get("kind")
        column = Column(name=name, type=kind.sql(dialect="postgres").lower() if kind else "text")
        for constraint in coldef.args.get("constraints") or []:
            kind_node = constraint.args.get("kind")
            if isinstance(kind_node, exp.NotNullColumnConstraint):
                column.nullable = bool(kind_node.args.get("allow_null"))
            elif isinstance(kind_node, exp.PrimaryKeyColumnConstraint):
                table.primary_key = [name]
                column.nullable = False
            elif isinstance(kind_node, exp.DefaultColumnConstraint):
                column.default = kind_node.this.sql(dialect="postgres") if kind_node.this else None
            elif isinstance(kind_node, exp.CheckColumnConstraint):
                _, values = _literal_values(kind_node.this)
                column.allowed_values = values or column.allowed_values
            elif isinstance(kind_node, exp.Reference) and isinstance(kind_node.this, exp.Schema):
                table.foreign_keys.append(ForeignKey(
                    columns=[name],
                    ref_table=_name(kind_node.this.this),
                    ref_columns=[_name(e) for e in kind_node.this.expressions] or [name],
                ))
        existing = table.column(name)
        if existing is not None:
            table.columns.remove(existing)
        table.columns.append(column)

    def _create_index(self, parsed: exp.Create) -> None:
        index = parsed.this
        if not isinstance(index, exp.Index):
            return
        table_name = _name(index.args.get("table"))
        table = self.catalog.table(table_name)
        if table is None:
            return
        params = index.args.get("params")
        columns = []
        for ordered in (params.args.get("columns") if params else None) or []:
            target = ordered.this if isinstance(ordered, exp.Ordered) else ordered
            columns.append(target.name.lower() if isinstance(target, exp.Column) else target.sql(dialect="postgres"))
        table.indexes.append(Index(
            name=_name(index.this),
            columns=columns,
            unique=bool(parsed.args.get("unique")),
            definition=parsed.sql(dialect="postgres"),
        ))

    def _create_view(self, parsed: exp.Create) -> None:
        target = parsed.this
        name = _name(target.this if isinstance(target, exp.Schema) else target)
        view = Table(name=name, is_view=True)
        query = parsed.expression
        if isinstance(query, exp.Query):
            for projection in query.selects:
                if projection.alias_or_name and projection.alias_or_name != "*":
                    view.columns.append(Column(name=projection.alias_or_name.lower(), type="unknown"))
        self.catalog.add_table(view)

    def _alter_table(self, parsed: exp.Alter) -> None:
        table = self._table(_name(parsed.this))
        for action in parsed.args.get("actions") or []:
            if isinstance(action, exp.ColumnDef):
                self._add_column(table, action)
            elif isinstance(action, exp.Drop) and (action.args.get("kind") or "").upper() == "COLUMN":
                for col in action.args.get("tables") or [action.this]:
                    existing = table.column(_name(col))
                    if existing:
                        table.columns.remove(existing)
            elif isinstance(action, exp.RenameColumn):
                existing = table.column(_name(action.this))
                if existing:
                    existing.name = _name(action.args.get("to"))
            elif isinstance(action, exp.AlterRename):
                self.catalog.tables.pop(table.name.lower(), None)
                table.name = _name(action.this)
                self.catalog.add_table(table)
            elif isinstance(action, exp.AlterColumn):
                existing = table.column(_name(action.this))
                if existing:
                    if action.args.get("dtype") is not None:
                        existing.type = action.args["dtype"].sql(dialect="postgres").lower()
                    if action.args.get("allow_null") is not None:
                        existing.nullable = bool(action.args["allow_null"])
            elif isinstance(action, exp.AddConstraint):
                for item in action.expressions:
                    self._apply_table_element(table, item)

    def _comment(self, parsed: exp.Comment) -> None:
        kind = (parsed.args.get("kind") or "").lower()
        text = parsed.expression.this if isinstance(parsed.expression, exp.Literal) else None
        if kind == "table":
            table = self.catalog.table(_name(parsed.this))
            if table:
                table.comment = text
        elif kind == "column" and isinstance(parsed.this, exp.Column):
            table = self.catalog.table(_name(parsed.this.args.get("table")))
            column = table.column(parsed.this.name) if table else None
            if column:
                column.comment = text


def catalog_from_sql(sql: str, source: str = "db") -> Catalog:
    catalog = Catalog(source=source)
    replayer = _Replayer(catalog)
    for statement in split_statements(sql):
        if not _DDL_OF_INTEREST.match(statement) or _SKIP_CREATE.match(statement):
            continue
        parsed = _parse(statement)
        if parsed is None:
            if not _lenient_statement(statement, catalog):
                catalog.problems.append(statement.splitlines()[0][:160])
            continue
        try:
            replayer.apply(parsed)
        except Exception as error:  # noqa: BLE001 - one odd statement must not stop the whole file
            catalog.problems.append(f"{statement.splitlines()[0][:120]} ({error})")
    return catalog


def catalog_from_file(path: str | Path, source: str = "db") -> Catalog:
    return catalog_from_sql(Path(path).expanduser().read_text(errors="replace"), source=source)
