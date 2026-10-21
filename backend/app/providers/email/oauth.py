"""OAuth 2.0 (authorization code + PKCE) for Gmail and Microsoft 365, used with XOAUTH2.

Requires the operator to register their own OAuth client and supply its id/secret
via environment variables. Not verifiable without those credentials.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings


@dataclass(frozen=True)
class OAuthProviderConfig:
    name: str
    authorize_url: str
    token_url: str
    scopes: tuple[str, ...]
    smtp_host: str
    smtp_port: int
    imap_host: str
    imap_port: int
    scope_explanation: str


def provider_config(provider: str) -> OAuthProviderConfig:
    s = get_settings()
    if provider == "gmail_oauth":
        return OAuthProviderConfig(
            name="gmail_oauth",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",  # noqa: S106 - public endpoint URL
            scopes=("https://mail.google.com/",),
            smtp_host="smtp.gmail.com",
            smtp_port=465,
            imap_host="imap.gmail.com",
            imap_port=993,
            scope_explanation=(
                "Google only allows SMTP/IMAP via OAuth with the full 'https://mail.google.com/' scope. "
                "It grants read/send access to the mailbox; the app only sends messages you approve and "
                "reads the inbox (read-only, without marking messages as read) to detect replies and bounces."
            ),
        )
    if provider == "microsoft_oauth":
        tenant = s.microsoft_tenant or "common"
        return OAuthProviderConfig(
            name="microsoft_oauth",
            authorize_url=f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
            token_url=f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            scopes=(
                "https://outlook.office.com/SMTP.Send",
                "https://outlook.office.com/IMAP.AccessAsUser.All",
                "offline_access",
            ),
            smtp_host="smtp.office365.com",
            smtp_port=587,
            imap_host="outlook.office365.com",
            imap_port=993,
            scope_explanation=(
                "SMTP.Send lets the app send as you; IMAP.AccessAsUser.All lets it read the inbox to detect "
                "replies/bounces; offline_access issues a refresh token so you don't have to sign in each hour. "
                "SMTP AUTH must be enabled for the mailbox by your Microsoft 365 admin."
            ),
        )
    raise ValueError(f"Unknown OAuth provider {provider}")


def client_credentials(provider: str) -> tuple[str, str]:
    s = get_settings()
    if provider == "gmail_oauth":
        return s.google_client_id, s.google_client_secret
    return s.microsoft_client_id, s.microsoft_client_secret


def is_configured(provider: str) -> bool:
    cid, secret = client_credentials(provider)
    return bool(cid and secret)


def redirect_uri() -> str:
    return get_settings().oauth_redirect_base_url.rstrip("/") + "/api/accounts/oauth/callback"


def build_authorize_url(provider: str, state: str, code_verifier: str, login_hint: str | None) -> str:
    cfg = provider_config(provider)
    cid, _ = client_credentials(provider)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
    params: dict[str, str] = {
        "client_id": cid,
        "response_type": "code",
        "redirect_uri": redirect_uri(),
        "scope": " ".join(cfg.scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if provider == "gmail_oauth":
        params.update({"access_type": "offline", "prompt": "consent"})
    if login_hint:
        params["login_hint"] = login_hint
    return cfg.authorize_url + "?" + urlencode(params)


def new_code_verifier() -> str:
    return secrets.token_urlsafe(64)


def exchange_code(provider: str, code: str, code_verifier: str, client: httpx.Client | None = None) -> dict[str, Any]:
    cfg = provider_config(provider)
    cid, secret = client_credentials(provider)
    data = {
        "client_id": cid,
        "client_secret": secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri(),
        "code_verifier": code_verifier,
    }
    c = client or httpx.Client(timeout=30)
    resp = c.post(cfg.token_url, data=data)
    if resp.status_code != 200:
        raise OAuthError(f"Token exchange failed ({resp.status_code})")
    return _normalize_tokens(resp.json())


def refresh(provider: str, refresh_token: str, client: httpx.Client | None = None) -> dict[str, Any]:
    cfg = provider_config(provider)
    cid, secret = client_credentials(provider)
    c = client or httpx.Client(timeout=30)
    resp = c.post(
        cfg.token_url,
        data={"client_id": cid, "client_secret": secret, "refresh_token": refresh_token, "grant_type": "refresh_token"},
    )
    if resp.status_code in (400, 401):
        raise OAuthError("Refresh token rejected — access was revoked or expired; reconnect the account", revoked=True)
    if resp.status_code != 200:
        raise OAuthError(f"Token refresh failed ({resp.status_code})")
    tokens = _normalize_tokens(resp.json())
    tokens.setdefault("refresh_token", refresh_token)  # Google omits it on refresh
    if not tokens.get("refresh_token"):
        tokens["refresh_token"] = refresh_token
    return tokens


def _normalize_tokens(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "access_token": payload.get("access_token"),
        "refresh_token": payload.get("refresh_token"),
        "expires_at": int(time.time()) + int(payload.get("expires_in", 3600)) - 60,
        "scope": payload.get("scope"),
    }


class OAuthError(Exception):
    def __init__(self, message: str, revoked: bool = False):
        super().__init__(message)
        self.message = message
        self.revoked = revoked
