"""Optional AES-GCM encryption and an encrypted local secret vault."""

from __future__ import annotations

import base64
import os
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from .db import utc_now
from .errors import DaythreadError

try:  # optional dependency: pip install daythread[crypto]
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    HAVE_CRYPTO = True
except Exception:  # noqa: BLE001 - keep the stdlib-only baseline importable
    AESGCM = None
    Scrypt = None
    HAVE_CRYPTO = False

SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
KEY_LENGTH = 32


class CryptoUnavailable(DaythreadError):
    code = "crypto_unavailable"
    status = 501


def ensure_crypto() -> None:
    if not HAVE_CRYPTO:
        raise CryptoUnavailable(
            "encryption requires the optional dependency: pip install 'daythread[crypto]'"
        )


def random_bytes(length: int) -> bytes:
    return os.urandom(length)


def derive_key(passphrase: str, salt: bytes) -> bytes:
    ensure_crypto()
    return Scrypt(salt=salt, length=KEY_LENGTH, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(
        passphrase.encode("utf-8")
    )


def encrypt(passphrase: str, plaintext: bytes) -> Dict[str, str]:
    ensure_crypto()
    salt = random_bytes(16)
    nonce = random_bytes(12)
    key = derive_key(passphrase, salt)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
    return {
        "salt": _b64(salt),
        "nonce": _b64(nonce),
        "ciphertext": _b64(ciphertext),
    }


def decrypt(passphrase: str, envelope: Dict[str, str]) -> bytes:
    ensure_crypto()
    try:
        salt = _unb64(envelope["salt"])
        nonce = _unb64(envelope["nonce"])
        ciphertext = _unb64(envelope["ciphertext"])
    except (KeyError, ValueError) as exc:
        raise DaythreadError("invalid encrypted envelope: {}".format(exc))
    key = derive_key(passphrase, salt)
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, None)
    except Exception:  # noqa: BLE001 - authentication failure
        raise DaythreadError("decryption failed (wrong passphrase or corrupted data)")


def encrypt_with_key(key: bytes, plaintext: bytes) -> Dict[str, str]:
    ensure_crypto()
    nonce = random_bytes(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
    return {"nonce": _b64(nonce), "ciphertext": _b64(ciphertext)}


def decrypt_with_key(key: bytes, nonce: str, ciphertext: str) -> bytes:
    ensure_crypto()
    try:
        return AESGCM(key).decrypt(_unb64(nonce), _unb64(ciphertext), None)
    except Exception:  # noqa: BLE001
        raise DaythreadError("decryption failed (wrong key or corrupted data)")


def load_or_create_master_key(home: Path) -> bytes:
    ensure_crypto()
    path = Path(home) / "master.key"
    if path.exists():
        key = path.read_bytes()
        if len(key) != KEY_LENGTH:
            raise DaythreadError("master.key must be {} bytes".format(KEY_LENGTH))
        return key
    key = random_bytes(KEY_LENGTH)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key)
    return key


class SecretVault:
    def __init__(self, conn: sqlite3.Connection, home: Path) -> None:
        self.conn = conn
        self.home = Path(home)
        self.key = load_or_create_master_key(self.home)

    def set(self, name: str, value: str) -> None:
        if not name:
            raise ValueError("secret name is required")
        envelope = encrypt_with_key(self.key, value.encode("utf-8"))
        now = utc_now()
        self.conn.execute(
            """
            INSERT OR REPLACE INTO secrets(name, nonce, ciphertext, created_at, updated_at)
            VALUES(?, ?, ?, COALESCE((SELECT created_at FROM secrets WHERE name = ?), ?), ?)
            """,
            (name, envelope["nonce"], envelope["ciphertext"], name, now, now),
        )
        self.conn.commit()

    def get(self, name: str) -> str:
        row = self.conn.execute("SELECT * FROM secrets WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise KeyError("secret not found: {}".format(name))
        return decrypt_with_key(self.key, row["nonce"], row["ciphertext"]).decode("utf-8")

    def names(self) -> List[str]:
        return [row["name"] for row in self.conn.execute("SELECT name FROM secrets ORDER BY name")]

    def delete(self, name: str) -> None:
        self.conn.execute("DELETE FROM secrets WHERE name = ?", (name,))
        self.conn.commit()

    def resolve(self, reference: str) -> Optional[str]:
        if not reference.startswith("secret:"):
            return None
        return self.get(reference[len("secret:"):])


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"))
