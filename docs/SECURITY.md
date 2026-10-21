# Security notes

| Area | Implementation |
|---|---|
| Network | Binds to `127.0.0.1` by default; Docker publishes only on host loopback. |
| Authentication | Single admin password (scrypt, N=2¹⁴). First-run setup only from loopback. Login rate-limited (5/min/IP); failed attempts audited. Changing the password invalidates all sessions. |
| Sessions | HMAC-signed token in an HttpOnly, SameSite=Strict cookie (`Secure` with `COOKIE_SECURE=true`), 72 h max age. |
| CSRF | Every non-GET request needs `X-CSRF-Token` equal to the `po_csrf` cookie **and** to the value bound inside the signed session. |
| Secrets at rest | SMTP/IMAP passwords, OAuth tokens and the AI key are Fernet-encrypted (`ENCRYPTION_KEY`). API responses expose only presence flags. |
| Logging | Root handler redacts known secret values and `password=`/`token=`/`Bearer`/`AUTH` patterns. Request logs contain method, path, status and timing only — no query strings or bodies. Email bodies are never logged. |
| Input | Pydantic validation on every endpoint; request body limit 1 MB (10 MB for CSV upload, enforced on streamed bytes); CSV: extension, binary sniffing, row/column caps. |
| Output | Security headers (CSP `default-src 'self'`, `X-Frame-Options: DENY`, `nosniff`, `no-referrer`), `Cache-Control: no-store` on API responses, consistent error bodies without stack traces. |
| SQL | SQLAlchemy expressions only — all values are bound parameters; no SQL is built from strings. |
| HTML | Template HTML sanitized with `nh3` (allow-list); variables HTML-escaped; inbound mail is rendered as plain text only. |
| CSV export | Values starting with `= + - @ \t \r` are prefixed with `'` (formula injection). |
| Unsubscribe | Tokens are Fernet-encrypted (authenticated, unguessable) — no readable email in the URL. GET shows a confirmation page and never changes state; POST (or RFC 8058 one-click) applies it. Rate-limited. |
| OAuth | Authorization code + PKCE; single-use, server-stored, 15-minute `state`. |
| Lead discovery | SSRF-safe fetcher (public IPs only, per-hop and peer-IP checks, ports 80/443, robots.txt, size/type/redirect caps), `defusedxml` for feeds, untrusted evidence never executed and fenced off in AI prompts, provider keys encrypted and never returned, credit budgets for Apollo enrichment. |
| Dependencies | `pip-audit` clean at build time (cryptography pinned ≥ 50 to include a security fix). Run `pip-audit` and `npm audit` periodically. |

Residual risks: in-process rate limiting; no MFA; a stolen `backend/data/encryption.key`
together with the database reveals stored mailbox credentials — protect the data directory
and backups.
