# Architecture

A modular monolith: one FastAPI application and one worker process share the same
Python package and database. The React SPA talks only to `/api/*`.

```
Browser (React SPA) ──/api──▶ FastAPI routes ──▶ services ──▶ SQLAlchemy ──▶ SQLite / PostgreSQL
                                                     ▲                          ▲
Worker process (python -m app.workers.run) ──────────┘   claims scheduled_jobs ─┘
        │
        ├─▶ providers/email (SMTP send, IMAP read, OAuth tokens)
        └─▶ providers/ai (OpenAI-compatible, template, mock)  ← also used by the API for drafts
```

* **Routes** (`app/api/routes/*`) validate input, enforce auth/CSRF/rate limits, call a
  service, commit, and serialize. No business logic lives in handlers.
* **Services** (`app/services/*`) hold all domain rules: leads, imports, segments, templates,
  campaigns, sending, inbox sync, classification, bounces, suppression, analytics, AI, retention.
* **Providers** (`app/providers/*`) are adapters behind small interfaces (`EmailSender`,
  `InboxReader`, `AIProvider`). Tests swap them for fakes.

## Durable scheduling

There is no in-memory timer. Every unit of work is a row in `scheduled_jobs`
(`send_step`, `send_single`, `sync_inbox`) with a unique `idempotency_key`.
The worker loop:

1. **Recovers stale jobs** (stuck in `running` > `STALE_JOB_MINUTES`). If a `sending`
   attempt exists the outcome is unknown → attempt `unknown`, job/recipient `needs_review`,
   and it is **never resent automatically**. Otherwise the job returns to `pending`.
2. **Creates inbox-sync jobs** for connected IMAP accounts every N minutes.
3. **Claims due jobs** with a conditional `UPDATE … WHERE status='pending'` (atomic on SQLite
   and PostgreSQL; safe with several workers). Jobs of paused/cancelled campaigns are not claimed;
   during an emergency stop no send job is claimed.
4. **Processes** each job in its own session.

## Send pipeline (per job)

`emergency stop → campaign state/start/end → recipient state & expected step → already sent?
→ suppression (email + domain) → lead status → max emails → sending window (campaign TZ, DST-aware)
→ account connected & enabled (live) → global/account/campaign rolling limits + spacing
→ render (strict; unresolved placeholders abort) → per-account lock → commit attempt as
'sending' → SMTP → record result → schedule next step (wait + next window)`.

Failure handling: auth errors disable the account and pause the campaign (recipient untouched);
5xx with a mailbox-unknown code → hard bounce → suppression; other 5xx → recipient failed;
4xx/connection errors → exponential backoff (60s·2ⁿ, ±15% jitter, capped 6h) up to 5 attempts.

Dry-run attempts are stored with `is_simulation = true` and status `simulated`, are counted
separately for limits and analytics, and never construct an SMTP client.

## Reply handling

IMAP sync (incremental by UID with UIDVALIDITY cursor) → dedupe by Message-ID → bounce
(DSN) parsing → match by `In-Reply-To`/`References` to our outbound Message-IDs, else by a
known lead's address → strip quoted history → automated/OOO detection (headers + patterns) →
rule classification (optional AI) → stop the lead's active sequences on a human reply →
suppress on unsubscribe requests (configurable). All classifications are reviewable.

## Data model

See the table list in [PLAN.md](../PLAN.md). Notable choices:

* `template_versions` are immutable; `campaign_steps` pin the exact versions approved.
* `campaign_recipients` has `UNIQUE(campaign_id, lead_id)` — a lead in several selected lists
  gets one email per step.
* `send_attempts` keeps every attempt (`UNIQUE(idempotency_key, attempt_no)`).
* `suppressions` has no FK to leads, so it survives lead deletion.
* All timestamps are stored in UTC (a `UTCDateTime` type rejects naive datetimes).

## Lead discovery

See [DISCOVERY.md](DISCOVERY.md). Two extra job kinds (`discovery_run`, `signal_process`) run in the
same worker; services live in `app/services/discovery/` (config, fetcher, sources, apollo, search,
classifier, scoring, drafting, pipeline, usage) and the Gemini adapter in `app/providers/ai/gemini.py`.
Discovery never imports the sending engine.
