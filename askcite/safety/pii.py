"""Decide which columns are personal or secret, so queries can never read them.

People may ask "how many users completed KYC?", but never "show me their PAN numbers".
The detection is by column name, plus your own additions/exceptions in sources.yaml.
"""

from __future__ import annotations

import re

from askcite.schema.catalog import Catalog, Table

_SECRET = re.compile(
    r"(password|passwd|pwd|salt|otp|pin$|^pin|encrypted|secret|token|api_?key|private_?key|cvv|signature)", re.I
)
_PERSONAL = re.compile(
    r"(^|_)(pan|pan_?number|pan_?no|aadhaa?r|aadhar_?number|uid_?number|passport|voter_?id|driving_?licen[cs]e|"
    r"phone|mobile|mobile_?number|contact_?number|whatsapp|email|e_?mail|dob|date_?of_?birth|birth_?date|"
    r"account_?number|account_?no|acc_?no|bank_?account|vpa|upi_?id|card_?number|address|address_?line\d*|"
    r"street|pincode|pin_?code|zip|ip_?address|device_?id|latitude|longitude|geo|selfie|photo|image_?url|"
    r"document_?url|father_?name|mother_?name|spouse_?name|nominee_?name|full_?name|first_?name|last_?name|"
    r"middle_?name|user_?name|pan_?name|holder_?name|name_?as_?per)($|_)",
    re.I,
)
# Compact names without underscores, as used in older tables (e.g. usermobilenumber, useremail).
_PERSONAL_COMPACT = re.compile(
    r"(mobile|phone|email|pannumber|panno|aadhaa?r|accountnumber|accountno|dateofbirth|address|"
    r"profile_?(image|pic|photo)|"
    r"(customer|user|holder|investor|nominee|father|mother|spouse|account|pan|full|first|last|middle|legal|"
    r"beneficiary|guardian)_?name)",
    re.I,
)


_DOB = re.compile(r"(dob|birth)", re.I)


def column_kind(table: str, column: str, extra: set[str], allowed: set[str], col_type: str = "") -> str | None:
    """Return 'secret', 'personal' or None for a column."""
    qualified = f"{table}.{column}".lower()
    if qualified in allowed or column.lower() in allowed:
        return None
    if qualified in extra or column.lower() in extra:
        return "personal"
    lowered_type = (col_type or "").lower()
    if lowered_type.startswith("bool"):
        return None  # a yes/no flag such as email_verified is not personal data
    if lowered_type.startswith(("timestamp", "date", "time")) and not _DOB.search(column):
        return None  # e.g. email_sent_at
    if lowered_type.startswith(("big", "int", "small", "serial")) and column.lower().endswith("id"):
        return None  # numeric identifiers such as otpid are keys, not secrets
    if column.lower().endswith(("count", "attempts", "retries", "_flag")):
        return None  # counters such as phone_verified_count describe activity, not the person
    if _SECRET.search(column):
        return "secret"
    if _PERSONAL.search(column) or _PERSONAL_COMPACT.search(column):
        return "personal"
    return None


class SensitiveColumns:
    def __init__(self, catalog: Catalog, extra: list[str] | None = None, allowed: list[str] | None = None):
        extra_set = {e.lower() for e in (extra or [])}
        allowed_set = {a.lower() for a in (allowed or [])}
        self._by_table: dict[str, dict[str, str]] = {}
        for table in catalog.tables.values():
            kinds = {}
            for column in table.columns:
                kind = column_kind(table.name, column.name, extra_set, allowed_set, column.type)
                if kind:
                    kinds[column.name.lower()] = kind
            self._by_table[table.name.lower()] = kinds

    def for_table(self, table: Table | str) -> dict[str, str]:
        name = table.name if isinstance(table, Table) else table
        return self._by_table.get(name.lower(), {})

    def is_sensitive(self, table: str, column: str) -> bool:
        return column.lower() in self._by_table.get(table.lower(), {})
