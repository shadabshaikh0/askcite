"""Needs the local Postgres from `docker compose up -d postgres` (skipped otherwise)."""

import psycopg
import pytest

from askcite.data.runner import ReadOnlyRunner

pytestmark = pytest.mark.integration


def test_fake_rows_respect_allowed_values_and_foreign_keys(fake_db_url):
    runner = ReadOnlyRunner(fake_db_url)
    statuses = {row[0] for row in runner.run("select distinct status from orders").rows}
    assert statuses <= {"CREATED", "PAID", "SETTLED", "CANCELLED"}
    orphans = runner.run("select count(*) from orders o left join users u on u.user_id = o.user_id "
                         "where u.user_id is null").rows[0][0]
    assert orphans == 0


def test_runner_is_read_only_limited_and_timed(fake_db_url):
    runner = ReadOnlyRunner(fake_db_url, max_rows=10, timeout_seconds=1)
    result = runner.run("select order_id from orders")
    assert result.row_count == 10 and result.truncated
    with pytest.raises(psycopg.errors.QueryCanceled):
        runner.run("select pg_sleep(3)")
    # even if the guard were bypassed: a server-side cursor only accepts SELECT, and the transaction is read-only
    with pytest.raises((psycopg.errors.SyntaxError, psycopg.errors.ReadOnlySqlTransaction)):
        runner.run("update orders set amount = 0")
    with pytest.raises((psycopg.errors.FeatureNotSupported, psycopg.errors.ReadOnlySqlTransaction)):
        runner.run("with d as (delete from orders returning order_id) select count(*) from d")


def test_role_itself_cannot_write(fake_db_url):
    with psycopg.connect(fake_db_url, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("delete from orders")
