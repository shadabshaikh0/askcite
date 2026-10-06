"""Fake rows follow the schema's keys."""

from askcite.data.fakedb import FakeRows
from askcite.schema.catalog import Catalog, Column, ForeignKey, Table


def test_a_table_keyed_by_its_parent_gets_one_row_per_parent():
    users = Table(name="users", columns=[Column(name="id", type="bigint")], primary_key=["id"])
    kyc = Table(name="kyc_status", columns=[Column(name="user_id", type="bigint")], primary_key=["user_id"],
                foreign_keys=[ForeignKey(columns=["user_id"], ref_table="users", ref_columns=["id"])])
    rows = FakeRows(Catalog(tables={"users": users, "kyc_status": kyc}), rows_per_table=50)
    rows.keys[("users", "id")] = list(range(1, 51))
    user_ids = [rows.value(kyc, "user_id", "bigint", None, row) for row in range(50)]
    assert sorted(user_ids) == list(range(1, 51))
