# Personal Outreach

A private, single-user cold-email outreach platform you run on your own computer or server:
leads & companies, CSV import/export, lists and rule-based segments, a template engine,
an AI-assisted (or AI-free) writing workspace, **lead discovery** (signals → evidence → scored drafts for review),
campaigns with multi-step sequences,
a durable sending worker, IMAP reply/bounce detection, a unified inbox, a central
suppression list, and analytics computed from real records.

It is built for **legitimate, relevant, low-volume business outreach**. It defaults to
**dry-run mode**, binds to **localhost**, requires a password, and enforces opt-outs and
sending limits on every message. It contains no features for evading spam filters,
rotating identities, spoofing senders, or scraping private data.

> You (the operator) are responsible for complying with the laws that apply to you and
> your recipients (e.g. CAN-SPAM, GDPR/ePrivacy, CASL, PECR) and with your email
> provider's policies. See [docs/COMPLIANCE.md](docs/COMPLIANCE.md).

---

## Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.12+, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic |
| Database | SQLite (local default) or PostgreSQL (Docker Compose) |
| Background work | Separate worker process with a **database-backed durable job queue** (no Redis needed) |
| Email | SMTP sending, IMAP sync, Gmail/Microsoft 365 via OAuth 2 + XOAUTH2 (provider adapters) |
| AI | Provider-independent interface: OpenAI-compatible API, Google Gemini, deterministic template mode, mock |
| Lead discovery | RSS/Atom, company websites, manual/LinkedIn paste (no keys); optional Apollo and Google Custom Search — see [docs/DISCOVERY.md](docs/DISCOVERY.md) |
| Frontend | React 18 + TypeScript (strict) + Vite 6 + Tailwind v4, React Router 7, TanStack Query, React Hook Form + Zod, Recharts, Radix Dialog |

Architecture and data model: [PLAN.md](PLAN.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

```
backend/   FastAPI app (app/api, core, db, models, schemas, services, providers, workers), alembic/, tests/
frontend/  React SPA (src/app, components, features, pages, lib, types, test)
scripts/   backup.py, restore.py, dev_smtp.py (local SMTP sink)
docs/      architecture, operations, deployment, providers, security, compliance
```

---

## Quick start (local, Windows / macOS / Linux)

Requirements: Python 3.12+, Node 20+ (tested with Python 3.13 and Node 24).

```bash
# 1. Backend
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt     # macOS/Linux: .venv/bin/python
.venv/Scripts/alembic upgrade head                              # creates backend/data/outreach.db
cd ..

# 2. Frontend
npm --prefix frontend install
```

Run three processes (three terminals), from the repository root:

```bash
backend/.venv/Scripts/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

```bash
cd backend && .venv/Scripts/python -m app.workers.run
```

```bash
npm --prefix frontend run dev
```

Open http://127.0.0.1:5173 and create your admin password (first-run setup is only allowed
from localhost). The Vite dev server proxies `/api` to port 8000; if your API runs elsewhere set
`VITE_BACKEND_URL=http://127.0.0.1:<port>` before `npm run dev`, and use `-- --port <n>` to move Vite.

**Single-process alternative:** `npm --prefix frontend run build`, then start only the API (it serves
the built SPA from `frontend/dist`) plus the worker, and open http://127.0.0.1:8000.

Interactive API docs (disabled when `APP_ENV=production`): http://127.0.0.1:8000/api/docs

Stop everything with Ctrl+C in each terminal. The worker finishes its current job before exiting.

### Configuration

Copy `.env.example` to `.env` in the repo root. Every value has a safe default; nothing is
required for a local trial. Secrets you don't provide (`SECRET_KEY`, `ENCRYPTION_KEY`) are
generated on first run into `backend/data/` — **back those files up with the database**.

---

## A first campaign, safely

1. **Settings → Sender identity**: your real name, company, postal address, signature.
2. **Sending accounts → Add account** → **Test connection** (status only becomes *connected*
   after a real SMTP login succeeds) → toggle **Sending** on.
3. **Leads → Import CSV**: map columns, validate, preview (valid / invalid / duplicate /
   existing / suppressed), then import into a list. Existing leads are never overwritten.
4. **Templates**: write a short plain-text email; preview it against real leads; missing
   variables are flagged. Or use the **AI writing workspace** and approve a draft.
5. **Email sequences**: initial email → wait → follow-up(s).
6. **Campaigns → New**: choose account, sequence, lists/segments, window, limits.
   **Submit for review** → inspect eligible / excluded counts, missing fields, account status,
   estimated schedule → **Approve** → **Activate** (type the campaign name).
7. While in **dry-run mode** (default) the worker renders each message and records it as
   *simulated*; SMTP is never contacted. When you're satisfied, **Settings → Sending & safety**
   → turn dry-run off and type `ENABLE LIVE SENDING`. Campaigns keep the mode they were
   activated with, so duplicate a dry-run campaign to send it for real.

The red **Emergency stop** button in the header halts all sending immediately.

### Testing live sending without emailing anyone

```bash
backend/.venv/Scripts/python scripts/dev_smtp.py
```

This runs an SMTP sink on `127.0.0.1:1025` that stores messages as `.eml` files in
`backend/data/dev-mailbox/` and relays nothing. Add an account with host `127.0.0.1`,
port `1025`, security **None**, any username/password.

---

## Configuring an email account safely

* Use a mailbox **you own** on a domain you control, with SPF, DKIM and DMARC set up
  (the account card has a **DNS check** and setup guidance).
* **Generic SMTP/IMAP**: prefer an *app password* over your main password. Credentials are
  encrypted at rest (Fernet, `ENCRYPTION_KEY`), never returned by the API, and masked in logs.
  Use **Rotate password** to change them and **Disconnect** to delete them.
* **Gmail / Microsoft 365 (OAuth)**: register your own OAuth app and set
  `GOOGLE_CLIENT_ID/SECRET` or `MICROSOFT_CLIENT_ID/SECRET/TENANT`. Register the redirect URI
  `<OAUTH_REDIRECT_BASE_URL>/api/accounts/oauth/callback`. Scopes requested and why are shown in
  the UI and in [docs/PROVIDERS.md](docs/PROVIDERS.md). *The OAuth flow is implemented and unit-tested
  with mocks but has not been exercised against real Google/Microsoft tenants.*
* Start with low limits (e.g. 20–30/day, 2+ minutes apart) and raise gradually. Global, account
  and campaign limits all apply; the strictest wins. Never exceed your provider's quotas.
* IMAP sync is read-only (`BODY.PEEK`, so messages aren't marked as read). Only messages that match
  your outreach (by `In-Reply-To`/`References` or a known lead's address) are stored.

## Enabling AI

AI is optional. Without it, the writing workspace uses **template mode**: deterministic drafts
assembled only from your brief.

To use any OpenAI-compatible `/chat/completions` endpoint, either set in `.env`:

```
AI_PROVIDER=openai_compatible
AI_BASE_URL=https://api.openai.com/v1
AI_MODEL=<model name>
AI_API_KEY=<your key>
```

or use **Settings → AI** (the key is stored encrypted in the database). Outputs are validated
against a Pydantic schema; numbers not present in your inputs and stray placeholders are flagged
for review. AI can never approve or send anything. Optional AI reply classification sends reply text
to the provider and only produces suggestions.

---

## Lead discovery

**Lead discovery** (sidebar) finds business signals from sources you choose, stores the source URL and
evidence, scores relevance with visible reasons, and writes drafts into the review queue. It never sends
email or approves anything. With no API keys it already works with RSS/Atom feeds, company websites and
signals you paste yourself (including LinkedIn posts — LinkedIn is never fetched). Apollo, Google Custom
Search and Gemini are optional; all keys stay on the backend. Everything starts **disabled / test mode**.
Full guide: [docs/DISCOVERY.md](docs/DISCOVERY.md).

Upgrading an existing install: back up, then run the new migration:

```bash
backend/.venv/Scripts/python scripts/backup.py
```

```bash
cd backend && .venv/Scripts/alembic upgrade head
```

## Tests and quality checks

```bash
cd backend
.venv/Scripts/python -m pytest                 # 101 tests: unit + API integration, fresh migrated DB per test
.venv/Scripts/ruff check app tests
.venv/Scripts/python -m mypy app
.venv/Scripts/python -m pip_audit -r requirements.txt
cd ../frontend
npm run typecheck && npm run lint && npm test && npm run build
```

Tests use fake SMTP/IMAP providers, an in-process SMTP server on localhost, mocked HTTP for the AI
adapter, and isolated SQLite databases. No test contacts an external server.

## Docker (PostgreSQL + API + worker)

```bash
cp .env.example .env    # set SECRET_KEY, ENCRYPTION_KEY, ADMIN_PASSWORD, POSTGRES_PASSWORD
docker compose up -d --build
```

The API is published on `127.0.0.1:8000` only; PostgreSQL is not published. Migrations run in a
one-shot `migrate` service (Alembic `upgrade head`, additive only). Because requests reach the
container from the Docker network, first-run setup is refused there — set `ADMIN_PASSWORD`.
For remote access put HTTPS in front and read [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Backups

* SQLite: `backend/.venv/Scripts/python scripts/backup.py` → `backups/<timestamp>/` (database +
  key files). Restore with `scripts/restore.py backups/<timestamp>` (current files are moved aside, not deleted).
* PostgreSQL: `docker compose exec db pg_dump -U outreach outreach > backup.sql`.

Details and troubleshooting: [docs/OPERATIONS.md](docs/OPERATIONS.md).

---

## What is implemented

Everything listed in the navigation is a working screen backed by real API endpoints:
Dashboard, Leads (CRUD, search/filter/sort/paginate, bulk actions, duplicates & confirmed merge,
CSV import wizard, CSV export with formula-injection protection, activity/audit trail), Lists &
rule-based Segments (live SQL counts), Campaigns (full lifecycle, review, approval snapshot,
typed-name activation, pause/resume/cancel, recipient management, send log), Email sequences,
Templates (versioned, live preview, AI workspace), Inbox (threads, classification, notes, labels,
handled, stop follow-ups, suppress, re-match, confirmed replies), Sending accounts, Analytics,
Suppression list (bulk add, audited corrections), Settings (identity, safety, AI, retention,
audit log, password, backups).

## Known limitations and work not done

* **Lead discovery:** Apollo, Google Custom Search and Gemini adapters are tested only against mocked
  responses (no keys were available). Google Custom Search is closed to new customers and ends 2027-01-01.
  No authorised LinkedIn integration exists. Apollo People Search returns no emails, so Apollo prospects
  need (credit-consuming) enrichment before a draft can be written; credit usage is counted
  conservatively as 1 per enrichment call because Apollo exposes no reliable remaining-credit endpoint.

* **Provider delivery events**: no webhook adapters (SES, Postmark, SendGrid…). Generic SMTP cannot
  confirm delivery, so "delivered" stays 0; "accepted" means only that your SMTP server took the message.
* **OAuth (Gmail/Microsoft)**: implemented (PKCE, refresh, revocation handling, XOAUTH2) but only
  tested with mocks — needs your own OAuth app to verify.
* **IMAP**: the adapter is exercised through fakes; the end-to-end IMAP transport was not run against a
  real server in this build. Bounce detection relies on standard DSN (RFC 3464) reports; non-standard
  bounces are recorded as *uncertain* and never auto-suppress.
* **Gmail/Graph native APIs** are not used (SMTP/IMAP with XOAUTH2 instead), so Gmail thread IDs and
  provider message IDs are not stored.
* **Unsubscribe links** require a public HTTPS URL (`PUBLIC_BASE_URL`) that routes `/u/*` to the API.
  Without it, opt-out works by reply plus a `mailto:` List-Unsubscribe header.
* **Rate limiting** of public/expensive endpoints is in-process (single API process). Use a reverse-proxy
  limit if you run several API processes or expose `/u/*` publicly.
* **Single user** by design: one admin password, no roles.
* **Open/click tracking** is intentionally not implemented.
* Lead enrichment/research from external data sources is not included — only data you supply.
* `npm audit` reports a moderate advisory in **vitest** (dev-only test runner, not shipped); the fix
  requires a major vitest upgrade that was not done. Runtime dependencies audit clean.
* Frontend tests cover app load, navigation, setup and form validation; most screens were verified
  manually in a browser rather than by automated UI tests.
