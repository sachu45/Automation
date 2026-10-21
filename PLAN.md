# Implementation Plan — Personal Outreach

A private, single-user cold-email outreach platform. Modular monolith: one FastAPI
backend (HTTP API + a separate worker process sharing the same code), one React SPA.

## Environment found

Empty directory. Python 3.13, Node 24 / npm 11, git, Docker 29 available. No local
Redis or PostgreSQL. → SQLite for local dev, PostgreSQL via Docker Compose for deployment.

## Key architecture decisions

| Concern | Decision | Why |
|---|---|---|
| Backend | FastAPI + SQLAlchemy 2 (sync) + Pydantic 2 + Alembic | Sync ORM keeps transactions simple and testable; FastAPI runs sync handlers in a threadpool. |
| DB | SQLite (WAL) locally, PostgreSQL in Compose | Same models/migrations for both. |
| Durable scheduling | **Database-backed job queue** (`scheduled_jobs` table) + separate worker process (`python -m app.workers.run`) | Jobs survive restarts because they live in the DB; claiming is an atomic conditional `UPDATE`, safe on SQLite and Postgres. Avoids adding Redis for a single-user app. Redis is not required. |
| Duplicate-send prevention | Idempotency key per (recipient, step, mode) with a UNIQUE constraint on `send_attempts.idempotency_key`; attempt row is committed in state `sending` *before* SMTP is contacted; a crashed `sending` attempt becomes `unknown` and is never auto-resent | At-most-once semantics. |
| Email | Provider adapters: `smtp_imap` (generic), `gmail_oauth`, `microsoft_oauth` (XOAUTH2 over SMTP/IMAP) | Campaign logic only talks to `EmailProvider` interface. |
| Secrets | Fernet encryption (`cryptography`) with `APP_ENCRYPTION_KEY`; never serialized in API responses; log redaction filter | |
| Auth | Single admin password (scrypt hash), HMAC-signed HttpOnly SameSite=Strict session cookie, double-submit CSRF token, login rate limit. Binds to 127.0.0.1 by default. | |
| AI | `AIProvider` interface: `openai_compatible` (any `/chat/completions` endpoint), `template` (deterministic, no AI, default), `mock` (tests). Pydantic-validated JSON outputs. AI never sends. | |
| Templates | Own small `{{ var | fallback }}` engine with strict mode; HTML is sanitized (`nh3`) and variables escaped; text alternative generated. | Predictable, no code execution. |
| Opt-out | Signed unguessable tokens (HMAC), public `/u/<token>` confirmation page + RFC 8058 one-click POST, `List-Unsubscribe` headers, rate-limited. | |
| Frontend | React 18 + TS strict + Vite + Tailwind v4 + hand-written shadcn-style components (Radix primitives where needed) + React Router + TanStack Query + RHF + Zod + Recharts | |

## Data model (tables)

companies, leads, tags, lead_tags, lists, list_memberships, segments (JSON rule
filters compiled to SQL), email_accounts, account_credentials (encrypted),
sync_states (IMAP UIDVALIDITY/last UID cursors), templates, template_versions
(immutable), sequences, sequence_steps, campaigns, campaign_sources (lists/segments),
campaign_steps (snapshot pinned to template versions at approval), campaign_recipients,
scheduled_jobs, send_attempts, provider_events, conversations, messages,
reply_classifications, suppressions, audit_events, app_settings, csv_imports,
ai_prompts, ai_drafts, ai_requests (usage metadata only, no content).

## Campaign lifecycle

`draft → ready_for_review → approved → scheduled/active → paused → completed | cancelled`.
Approval snapshots steps + recipients; activation requires typed confirmation.
Every send re-checks: emergency stop, campaign status, window, account/campaign/global
limits, suppression (email + domain), lead status, recipient status, already-sent.

## Phases (all to be executed in this session)

1. Foundation: structure, config, DB, Alembic, health, auth, frontend shell.
2. Leads/companies/tags, CSV import (mapping → preview → commit) + export, lists, segments, templates, suppression.
3. Email providers (SMTP/IMAP/OAuth), dry-run, accounts screen, send attempts, rate limits, idempotency.
4. Campaigns, review, approval, scheduler/worker, sequences, pause/resume/cancel/emergency stop.
5. AI workspace (providers, structured output, saved prompts, drafts, approval).
6. Inbox sync (IMAP incremental), matching, bounce DSN parsing, classification, inbox UI.
7. Analytics + dashboard, audit log, security hardening, retention.
8. Tests (pytest + vitest), run app, browser verification, docs, final report.

## Out of scope / honest limits (tracked in README "Known limitations")

- No provider webhook adapters (SES/Postmark/etc.); generic SMTP cannot report delivery.
- OAuth flows implemented but unverifiable without real client credentials.
- Open tracking intentionally not implemented.

## Extension: lead discovery (migration 0002)

Pipeline inside the existing worker: `discovery_run` and `signal_process` jobs in `scheduled_jobs`.
Sources behind a `SignalSource` interface (RSS/Atom, company websites, manual/LinkedIn paste, Apollo,
Google Custom Search); constrained SSRF-safe fetcher; rule-based classifier; transparent weighted scoring
with hard exclusions; drafts saved as `ai_drafts` (origin=discovery, signal_id, generation_key unique)
using the existing AI provider interface plus a new Gemini provider, with template fallback.
New tables: discovery_signals, discovery_runs, provider_usage, fetched_pages. Never sends email.
Details: docs/DISCOVERY.md.
