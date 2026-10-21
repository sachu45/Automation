# Operations

## Processes

| Process | Command (from repo root) | Notes |
|---|---|---|
| API | `backend/.venv/Scripts/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000` | or `python -m app` inside `backend/` (uses `APP_HOST`/`APP_PORT`) |
| Worker | `cd backend && .venv/Scripts/python -m app.workers.run` | required for any sending or inbox sync; safe to restart at any time |
| Frontend (dev) | `npm --prefix frontend run dev` | or `npm --prefix frontend run build` and let the API serve `frontend/dist` |

On macOS/Linux replace `.venv/Scripts/` with `.venv/bin/`.

The dashboard warns when due jobs have waited >10 minutes ("worker may not be running").
`GET /api/health` returns database status and the oldest overdue job age.

## Migrations

`cd backend && .venv/Scripts/alembic upgrade head`. Migrations are additive; the app never drops
or recreates tables on start. Create new ones with
`alembic revision --autogenerate -m "describe change"` and review them before applying.

## Backups

**SQLite**

```bash
backend/.venv/Scripts/python scripts/backup.py                    # → backups/<UTC timestamp>/
backend/.venv/Scripts/python scripts/restore.py backups/<timestamp>  # stop API + worker first
```

The backup includes `encryption.key` and `secret.key`. Without `encryption.key` stored account
passwords/OAuth tokens and the stored AI key cannot be decrypted (you would need to re-enter them).
If you set `ENCRYPTION_KEY`/`SECRET_KEY` in `.env`, back up `.env` securely instead.

**PostgreSQL (Docker)**

```bash
docker compose exec db pg_dump -U outreach -Fc outreach > outreach.dump
docker compose exec -T db pg_restore -U outreach -d outreach --clean < outreach.dump
docker compose run --rm migrate
```

Encrypt backups at rest; they contain personal data.

## Data retention and deletion

* Settings → Data & retention: inbound/outbound message bodies and audit events older than N days
  are erased hourly by the worker (0 = keep). Metadata used for analytics is kept.
* Deleting leads removes their records and list memberships; conversations and attempts keep
  anonymised references. Suppression entries are deliberately kept.
* Raw CSV rows are purged after import, or after 24 h if an import is abandoned.
* AI usage logs keep metadata only and are pruned after 90 days.

## Suppression corrections

Manual / bounce / DNC entries can be lifted from the UI by retyping the address and giving a
reason (audited; the record is kept with `active = false`).

Recipient-originated opt-outs (unsubscribe link, one-click, reply) and complaints **cannot** be
lifted in the UI. If a person explicitly asks to be contacted again, keep evidence of their
request and run, against the database:

```sql
UPDATE suppressions SET active = false, lifted_at = CURRENT_TIMESTAMP,
       lifted_reason = 'Re-opt-in: <evidence reference>' WHERE email = '<address>';
INSERT INTO audit_events (action, summary, details, actor, created_at)
VALUES ('suppression.lifted_manual', 'Manual re-opt-in for <address>', '{}', 'admin', CURRENT_TIMESTAMP);
```

## Unknown send outcomes

If the worker dies while talking to SMTP, the recipient shows **needs review** and is never resent
automatically. Check your mailbox's Sent folder, then use "It was sent" or "Not sent — retry" on the
campaign's Recipients tab.

## Troubleshooting

| Symptom | Check |
|---|---|
| Nothing sends | Worker running? Emergency stop off? Campaign active (not paused)? Inside the sending window (campaign timezone)? Limits reached (send log shows "rate limited")? Live mode requires account *connected* + *sending enabled*. |
| Everything is "simulated" | The campaign was activated in dry-run mode. Turn dry-run off in Settings, then duplicate and re-activate the campaign. |
| Account shows `auth_failed` | Wrong/rotated password or revoked OAuth consent. Rotate credentials or re-authorize, then test. Dependent campaigns were paused — resume them. |
| Replies not appearing | IMAP enabled and connected? Correct folder? Only messages matching outreach headers or a known lead address are imported. |
| "Cannot reach the backend" in the UI | API not running, or the Vite proxy points to the wrong port (`VITE_BACKEND_URL`). |
| Port 8000/5173 already in use | Start the API with `--port 8001` and Vite with `VITE_BACKEND_URL=http://127.0.0.1:8001 npm --prefix frontend run dev -- --port 5180`. |
| Forgot admin password | Stop the API, then `DELETE FROM app_settings WHERE key IN ('auth_password_hash');` and run first-run setup again from localhost (or set `ADMIN_PASSWORD`). |
