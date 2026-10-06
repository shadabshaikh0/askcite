"""Encrypt connector secrets (tokens, passwords, SSH keys) before they are stored.

The key comes from ASKCITE_SECRET_KEY, or is generated once into <data dir>/secret.key
(readable by the owner only). Keep that file (or the variable) safe and backed up:
without it, saved secrets cannot be read and connectors must be re-entered.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretsUnavailable(RuntimeError):
    pass


def load_or_create_key(data_dir: Path) -> bytes:
    from_env = os.environ.get("ASKCITE_SECRET_KEY")
    if from_env:
        return from_env.encode()
    path = Path(data_dir) / "secret.key"
    if path.exists():
        return path.read_bytes().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(key)
    return key


class SecretBox:
    def __init__(self, key: bytes):
        self._fernet = Fernet(key)
        self._key = key

    def derive(self, label: bytes) -> bytes:
        """A separate secret for another purpose (e.g. CSRF tokens), derived from the same key."""
        import hashlib
        import hmac

        return hmac.new(self._key, label, hashlib.sha256).digest()

    @classmethod
    def for_data_dir(cls, data_dir: Path) -> SecretBox:
        return cls(load_or_create_key(data_dir))

    def encrypt(self, values: dict[str, str]) -> str:
        return self._fernet.encrypt(json.dumps(values).encode()).decode()

    def decrypt(self, token: str | None) -> dict[str, str]:
        if not token:
            return {}
        try:
            return json.loads(self._fernet.decrypt(token.encode()))
        except InvalidToken as error:
            raise SecretsUnavailable("saved secrets cannot be read with the current secret key") from error
