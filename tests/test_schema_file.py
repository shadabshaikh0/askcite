from askcite.sources.schema_file import catalog_from_sql, split_statements


def test_reads_messy_hand_written_schema(catalog):
    assert set(catalog.tables) == {"users", "orders", "payments"}
    assert catalog.problems == []


def test_create_table_with_trailing_comma_and_no_semicolon(catalog):
    users = catalog.table("users")
    assert [c.name for c in users.columns] == ["user_id", "full_name", "mobile_number", "email", "email_verified",
                                               "created_at"]
    assert users.primary_key == ["user_id"]


def test_alter_table_changes_are_replayed_in_order(catalog):
    orders = catalog.table("orders")
    names = [c.name for c in orders.columns]
    assert "settled_at" in names and "coupon_code" in names
    assert "promo_code" not in names and "channel" not in names
    assert orders.foreign_keys[0].ref_table == "users"
    assert orders.column("amount").comment == "order value in rupees"
    assert orders.indexes[0].columns == ["status"]


def test_check_lists_become_allowed_values(catalog):
    assert catalog.table("orders").column("status").allowed_values == ["CREATED", "PAID", "SETTLED", "CANCELLED"]
    # `check status in (...)` without parentheses is repaired
    assert catalog.table("payments").column("status").allowed_values == ["SUCCESS", "FAILED"]


def test_function_bodies_are_not_split():
    statements = split_statements("create function f() returns int as $$ begin; select 1; end; $$ language sql;\n"
                                  "create table t (a int);")
    assert len(statements) == 2


def test_missing_closing_parenthesis_does_not_swallow_next_table():
    catalog = catalog_from_sql("create table a (\n id int,\n name text\n\ncreate table b (id int);")
    assert set(catalog.tables) == {"a", "b"}
