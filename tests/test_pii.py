from askcite.safety.pii import SensitiveColumns, column_kind


def test_personal_and_secret_columns(catalog):
    sensitive = SensitiveColumns(catalog)
    assert set(sensitive.for_table("users")) == {"full_name", "mobile_number", "email"}
    assert sensitive.for_table("orders") == {}
    assert sensitive.is_sensitive("payments", "card_number")


def test_flags_ids_and_dates_are_not_personal():
    assert column_kind("t", "email_verified", set(), set(), "boolean") is None
    assert column_kind("t", "email_sent_at", set(), set(), "timestamp") is None
    assert column_kind("t", "otpid", set(), set(), "bigint") is None
    assert column_kind("t", "date_of_birth", set(), set(), "date") == "personal"
    assert column_kind("t", "password_hash", set(), set(), "text") == "secret"
    assert column_kind("t", "usermobilenumber", set(), set(), "text") == "personal"
    for column in ("customername", "customerphone", "customeremail", "profile_image", "nominee_name"):
        assert column_kind("t", column, set(), set(), "text") == "personal", column
    for column in ("productname", "securityname", "order_status", "phone_verified_count"):
        assert column_kind("t", column, set(), set(), "bigint" if "count" in column else "text") is None, column


def test_config_can_add_or_allow_columns():
    assert column_kind("orders", "notes", {"orders.notes"}, set(), "text") == "personal"
    assert column_kind("banners", "image_url", set(), {"banners.image_url"}, "text") is None
