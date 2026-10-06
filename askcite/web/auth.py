"""Protecting the web page: an admin password (HTTP Basic, user "admin") and CSRF tokens.

The password is ASKCITE_ADMIN_PASSWORD, or one generated on first start and stored hashed
(scrypt) in Askcite's database. `askcite admin-password` changes it.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets

import psycopg

_KEY = "admin_password"


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)


def set_admin_password(store: psycopg.Connection, password: str) -> None:
    salt = secrets.token_bytes(16)
    value = f"scrypt${salt.hex()}${_hash(password, salt).hex()}"
    store.execute("insert into app_setting (key, value) values (%s, %s) on conflict (key) do update "
                  "set value = excluded.value, updated_at = now()", (_KEY, value))


def ensure_admin_password(store: psycopg.Connection) -> str | None:
    """Create a password on first start. Returns it (to print once) if one was generated."""
    if os.environ.get("ASKCITE_ADMIN_PASSWORD"):
        return None
    if store.execute("select 1 from app_setting where key = %s", (_KEY,)).fetchone():
        return None
    password = secrets.token_urlsafe(12)
    set_admin_password(store, password)
    return password


class PasswordChecker:
    def __init__(self, store: psycopg.Connection):
        self.store = store
        self._accepted: set[bytes] = set()  # sha256 of passwords already verified (scrypt is slow on purpose)

    def check(self, username: str, password: str) -> bool:
        if not hmac.compare_digest(username.encode(), b"admin"):
            return False
        from_env = os.environ.get("ASKCITE_ADMIN_PASSWORD")
        if from_env:
            return hmac.compare_digest(password.encode(), from_env.encode())
        row = self.store.execute("select value from app_setting where key = %s", (_KEY,)).fetchone()
        if not row:
            return False
        digest = hashlib.sha256(row["value"].encode() + password.encode()).digest()
        if digest in self._accepted:
            return True
        _, salt_hex, hash_hex = row["value"].split("$")
        if hmac.compare_digest(_hash(password, bytes.fromhex(salt_hex)), bytes.fromhex(hash_hex)):
            self._accepted.add(digest)
            return True
        return False


def csrf_token(secret_key: bytes) -> str:
    """Same for every form of this deployment, but impossible to guess without the secret key."""
    return hmac.new(secret_key, b"askcite-csrf", hashlib.sha256).hexdigest()
