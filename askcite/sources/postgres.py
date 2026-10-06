"""Read the schema (never rows) from a live PostgreSQL database.

Only system-catalog queries are run here: table/column names, types, keys,
indexes, comments, CHECK constraints and the planner's row estimate.
"""

from __future__ import annotations

import re

import psycopg

from askcite.schema.catalog import Catalog, Column, ForeignKey, Index, Table

_COLUMNS = """
select c.relname as table_name,
       c.relkind = 'v' or c.relkind = 'm' as is_view,
       a.attname as column_name,
       format_type(a.atttypid, a.atttypmod) as data_type,
       not a.attnotnull as nullable,
       pg_get_expr(d.adbin, d.adrelid) as default_expr,
       col_description(c.oid, a.attnum) as column_comment,
       obj_description(c.oid, 'pg_class') as table_comment,
       greatest(c.reltuples, 0)::bigint as estimated_rows
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
left join pg_attrdef d on d.adrelid = c.oid and d.adnum = a.attnum
where n.nspname = any(%(schemas)s)
  and c.relkind in ('r', 'p', 'v', 'm')
order by c.relname, a.attnum
"""

_CONSTRAINTS = """
select c.relname as table_name,
       con.contype as kind,
       pg_get_constraintdef(con.oid) as definition,
       (select array_agg(att.attname order by k.ord)
          from unnest(con.conkey) with ordinality k(attnum, ord)
          join pg_attribute att on att.attrelid = con.conrelid and att.attnum = k.attnum) as columns,
       ref.relname as ref_table,
       (select array_agg(att.attname order by k.ord)
          from unnest(con.confkey) with ordinality k(attnum, ord)
          join pg_attribute att on att.attrelid = con.confrelid and att.attnum = k.attnum) as ref_columns
from pg_constraint con
join pg_class c on c.oid = con.conrelid
join pg_namespace n on n.oid = c.relnamespace
left join pg_class ref on ref.oid = con.confrelid
where n.nspname = any(%(schemas)s)
  and con.contype in ('p', 'f', 'c')
"""

_INDEXES = """
select t.relname as table_name,
       i.relname as index_name,
       ix.indisunique as is_unique,
       pg_get_indexdef(ix.indexrelid) as definition,
       (select array_agg(a.attname order by k.ord)
          from unnest(ix.indkey) with ordinality k(attnum, ord)
          join pg_attribute a on a.attrelid = t.oid and a.attnum = k.attnum) as columns
from pg_index ix
join pg_class t on t.oid = ix.indrelid
join pg_class i on i.oid = ix.indexrelid
join pg_namespace n on n.oid = t.relnamespace
where n.nspname = any(%(schemas)s)
"""

_IN_LIST = re.compile(r"\(\(?\s*\(?(\w+)\)?(?:::\w+)?\s*=\s*any\s*\(\s*\(?array\[(.*?)\]", re.IGNORECASE)


def _check_values(definition: str) -> tuple[str | None, list[str] | None]:
    """pg_get_constraintdef renders `col IN ('A','B')` as `((col = ANY (ARRAY['A'::text, 'B'::text])))`."""
    match = _IN_LIST.search(definition or "")
    if not match:
        return None, None
    return match.group(1).lower(), re.findall(r"'((?:[^']|'')*)'", match.group(2)) or None


def catalog_from_database(url: str, schemas: list[str], source: str = "db") -> Catalog:
    catalog = Catalog(source=source)
    params = {"schemas": schemas}
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("set default_transaction_read_only = on")
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(_COLUMNS, params)
            for row in cur.fetchall():
                table = catalog.table(row["table_name"])
                if table is None:
                    table = Table(name=row["table_name"], is_view=row["is_view"], comment=row["table_comment"],
                                  estimated_rows=row["estimated_rows"])
                    catalog.add_table(table)
                table.columns.append(Column(
                    name=row["column_name"], type=row["data_type"], nullable=row["nullable"],
                    default=row["default_expr"], comment=row["column_comment"],
                ))
            cur.execute(_CONSTRAINTS, params)
            for row in cur.fetchall():
                table = catalog.table(row["table_name"])
                if table is None:
                    continue
                if row["kind"] == "p":
                    table.primary_key = list(row["columns"] or [])
                elif row["kind"] == "f":
                    table.foreign_keys.append(ForeignKey(
                        columns=list(row["columns"] or []), ref_table=row["ref_table"],
                        ref_columns=list(row["ref_columns"] or []),
                    ))
                elif row["kind"] == "c":
                    column_name, values = _check_values(row["definition"])
                    column = table.column(column_name) if column_name else None
                    if column is not None and values:
                        column.allowed_values = values
            cur.execute(_INDEXES, params)
            for row in cur.fetchall():
                table = catalog.table(row["table_name"])
                if table is not None:
                    table.indexes.append(Index(
                        name=row["index_name"], unique=row["is_unique"], definition=row["definition"],
                        columns=[c for c in (row["columns"] or []) if c],
                    ))
    return catalog
