import pytest

ALLOWED = [
    "select count(*) from orders where status = 'SETTLED'",
    "select status, count(*), sum(amount) from orders group by status order by 2 desc",
    "with recent as (select order_id, amount from orders where created_at > now() - interval '7 days') "
    "select count(*) from recent",
    "select o.status, count(distinct o.user_id) from orders o join users u on u.user_id = o.user_id group by 1",
    "select count(*) from users",
    "select orders.* from orders",
    "select u.email_verified, count(*) from users u group by 1",
]

BLOCKED = {
    "delete from orders": "only SELECT",
    "update orders set status = 'PAID'": "only SELECT",
    "select 1; drop table orders": "one statement",
    "with d as (delete from orders returning *) select * from d": "DELETE",
    "select pg_sleep(60)": "pg_sleep",
    "select * from pg_catalog.pg_user": "system tables",
    "select * from information_schema.tables": "system tables",
    "select * from salaries": "unknown table",
    "select * into copy_of_orders from orders": "INTO",
    "select * from orders for update": "LOCK",
    "select query_to_xml('select * from users', true, true, '')": "query_to_xml",
    "select * from users": "'*' is not allowed",
    "select u.* from users u": "'*' is not allowed",
    "select full_name from users": "personal",
    "select u.user_id, u.mobile_number from users u": "personal",
    "select row_to_json(u) from users u": "whole-row",
    "select count(*) from orders o join users u on u.user_id = o.user_id where u.email like '%gmail%'": "personal",
    "select card_number from payments": "personal",
    "select dblink('host=x', 'select 1')": "dblink",
    "copy orders to '/tmp/x'": "only SELECT",
}


@pytest.mark.parametrize("sql", ALLOWED)
def test_safe_reads_are_allowed(guard, sql):
    result = guard.check(sql)
    assert result.ok, result.errors


@pytest.mark.parametrize("sql,reason", BLOCKED.items())
def test_unsafe_queries_are_blocked(guard, sql, reason):
    result = guard.check(sql)
    assert not result.ok
    assert reason.lower() in " ".join(result.errors).lower()


def test_approval_tables_are_flagged(guard):
    result = guard.check("select count(*) from payments where status = 'FAILED'")
    assert result.ok and result.needs_approval
    assert result.tables == ["payments"]
