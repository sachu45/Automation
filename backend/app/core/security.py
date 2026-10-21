"""Password hashing, signed tokens, and credential encryption."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

# --- Passwords (scrypt, stdlib) -------------------------------------------------

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        candidate = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=len(_unb64(digest)))
        return hmac.compare_digest(candidate, _unb64(digest))
    except (ValueError, TypeError):
        return False


# --- Signed tokens -------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _sign(purpose: str, payload: bytes) -> str:
    key = hashlib.sha256((get_settings().secret_key + ":" + purpose).encode()).digest()
    return _b64(hmac.new(key, payload, hashlib.sha256).digest())


def make_token(purpose: str, data: dict[str, Any], max_age_seconds: int | None = None) -> str:
    body = dict(data)
    body["iat"] = int(time.time())
    if max_age_seconds:
        body["exp"] = body["iat"] + max_age_seconds
    payload = _b64(json.dumps(body, separators=(",", ":")).encode())
    return f"{payload}.{_sign(purpose, payload.encode())}"


def read_token(purpose: str, token: str) -> dict[str, Any] | None:
    try:
        payload, sig = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(purpose, payload.encode())):
        return None
    try:
        data = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError):
        return None
    if "exp" in data and time.time() > data["exp"]:
        return None
    return data


def make_unsubscribe_token(recipient_ref: int, email_hash: str) -> str:
    """Opaque token: carries an internal id plus a hash prefix, never the address itself."""
    return make_token("unsubscribe", {"r": recipient_ref, "h": email_hash})


def email_fingerprint(email: str) -> str:
    key = hashlib.sha256((get_settings().secret_key + ":email").encode()).digest()
    return hmac.new(key, email.lower().encode(), hashlib.sha256).hexdigest()[:16]


# --- Credential encryption -------------------------------------------------------


def _fernet() -> Fernet:
    return Fernet(get_settings().encryption_key.encode())


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Stored credential could not be decrypted (encryption key changed?)") from exc
