# Lead discovery

Finds business signals (hiring announcements, launches, expansions, keyword matches) from
sources you choose, stores the evidence, scores relevance transparently and writes **drafts**
into the existing AI-draft review workflow. **Discovery never sends email, approves drafts,
creates campaigns or changes sending settings.**

## Data flow

```
worker tick ──▶ ensure_scheduled_run (only if discovery is enabled)
            └─▶ discovery_run job ──▶ enabled sources ──▶ RawSignal[] ──▶ insert (UNIQUE dedupe_key)
                                                                     └─▶ signal_process job (one per new signal)
signal_process ──▶ company-page evidence (constrained fetcher)
               ──▶ match company / lead (by verified email only) ── optional Apollo enrichment (credit budget)
               ──▶ hard exclusions (suppression, excluded domains/companies/keywords, contacted recently, blocked status)
               ──▶ weighted score (industry, geography, size, signal, persona, evidence) ──▶ qualified | review_required | rejected
               ──▶ draft (Gemini / OpenAI-compatible / template; template fallback) ──▶ validation warnings
               ──▶ ai_drafts row: origin=discovery, signal_id, generation_key, content_hash, status=draft
```

Both job types use the existing `scheduled_jobs` queue: atomic claims, bounded retries with
exponential backoff and jitter, permanent-failure classification, and crash recovery through
the existing stale-job sweep. A draft is created at most once per `signal:<id>:v<n>`
(unique `ai_drafts.generation_key`); reprocessing creates `v<n+1>` explicitly.

## Sources

| Source | Key | Cost / limits | Status |
|---|---|---|---|
| RSS / Atom feeds | none | free | **Works.** Verified live against a public feed (GitHub Blog). XML parsed with `defusedxml`. |
| Company websites | none | free | **Works.** Homepage + same-domain About/Careers/News/Contact pages, ≤ `max_pages_per_domain`. Verified live. |
| Manual / LinkedIn paste | none | free | **Works.** You paste a URL and the text you're authorised to use. LinkedIn URLs are never fetched. |
| Apollo.io | `APOLLO_API_KEY` or Sources tab | People Search: 0 credits, **no emails** (last names obfuscated). Enrichment `/people/match`: 1+ credits/person | Implemented against the documented API; **tested with mocked responses only**. |
| Google Custom Search | `GOOGLE_SEARCH_API_KEY` + `GOOGLE_SEARCH_ENGINE_ID` | your Google Cloud quota | **Closed to new customers; shuts down 2027-01-01.** Adapter is for existing projects only; mock-tested. |
| Gemini (drafting) | `GEMINI_API_KEY` or Sources tab; select *gemini* in Settings → AI | free tier on some models, low daily limits (see aistudio.google.com/rate-limit) | Implemented (REST `generateContent`, JSON output, key in `x-goog-api-key` header); **mock-tested only**. Falls back to the template writer on any error. |

No authorised LinkedIn API integration exists in this app; reading other people's posts is not
something an ordinary registered LinkedIn app can do.

## Getting keys (all optional)

* **Apollo:** Apollo app → Settings → Integrations → API Keys. Plans differ in API access and
  credits; check your plan. Keep *enrichment* off until you've decided on a daily credit budget.
* **Gemini:** https://aistudio.google.com → *Get API key*. Do not enable billing unless you mean to.
  Default model `gemini-2.5-flash` (configurable with `GEMINI_MODEL` or Settings → AI → Model).
* **Google Custom Search:** only if your project already has access; new projects can't sign up.

Keys entered in the UI are encrypted with `ENCRYPTION_KEY` and never returned to the browser;
environment variables are used when no stored key exists.

## Configuring targeting (Lead discovery → Rules)

* Industries, sizes (`11,50`), countries/regions, buyer roles, job titles, keywords, excluded
  keywords/domains/companies, signal categories, feeds and watched company domains.
* **Minimum score** (default 60): qualified signals at or above it get a draft (if a verified contact
  email exists). **Review threshold** (default 40): between the two, the signal waits for your review.
* Empty targeting lists are left out of the score instead of counting against a signal.
* **Test mode** (default on): no credit-consuming calls and no external AI; drafts are written by the
  template writer and labelled *test*.
* Limits: max new signals per run/day, AI drafts per run/day, Apollo credit budget per day,
  pages per domain, evidence characters sent to the AI.

## Starting and stopping

* **Run once:** Lead discovery → Overview → *Run now* (queued for the worker).
* **Schedule:** *Enable schedule* (asks for confirmation). Runs every `interval_minutes`, one at a time.
* **Pause everything:** *Pause* (sets `enabled=false`). Per-source on/off: Sources tab checkboxes.
* The worker must be running (`python -m app.workers.run`).

## Reviewing drafts

Lead discovery → *Draft review* shows each draft with its lead, the signal, the evidence and any
validation warnings (unsupported numbers, implied prior relationship, LinkedIn claims for non-LinkedIn
sources, instruction-like text from untrusted evidence, unknown links). Edit, *Save*, *Regenerate*
(new version) or *Approve…* (shows the exact text). Approval doesn't send; send from the lead's page,
where suppression, dry-run, limits and the final rendered preview still apply.

## Evidence and retention

Each signal keeps source URL, provider, retrieval time, excerpt and evidence items. Evidence of
rejected/archived/failed signals is erased after `evidence_retention_days` (default 180); the page
cache is erased after 30 days. *Delete evidence* on a signal erases it immediately.

## Safety limits of the fetcher

http/https on ports 80/443 only; no URL credentials; every hostname and redirect hop must resolve
to public IPs, and the connected peer IP is re-checked (DNS-rebinding guard); robots.txt is honoured
(RFC 9309); 1.5 MB response cap; HTML/feeds/text only; 3 redirects max; per-domain delay; ETag/
Last-Modified conditional requests. Fetched text is treated as untrusted data — it's never executed,
and the drafting prompt tells the model to ignore instructions inside it.

## Recovering from failures

* *Overview → Failed jobs* lists permanently failed discovery jobs with their errors.
* Fix the cause (key, feed URL, rules), then *Reprocess* the signal from the Signal inbox.
* A crashed worker's running jobs are returned to the queue automatically after `STALE_JOB_MINUTES`.

## Migration

`0002_lead_discovery` adds `discovery_signals`, `discovery_runs`, `provider_usage`, `fetched_pages`
and four nullable/defaulted columns on `ai_drafts`. It changes no existing data. To roll back without
data loss, simply leave discovery disabled; `alembic downgrade 0001` is available but drops the
discovery tables (only discovery data). Back up first: `python scripts/backup.py`.
