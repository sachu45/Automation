# Responsible outreach and compliance

This software helps you send relevant, honest, low-volume business email. **You are the
operator and are responsible for complying with the law in your and your recipients'
jurisdictions** (for example CAN-SPAM in the US, GDPR/ePrivacy and PECR in the EU/UK, CASL in
Canada — some of which require prior consent for commercial email to individuals) and with your
email provider's acceptable-use policy. This document is not legal advice.

## What the app does to help

* Honest identity: sender name/address come from your own connected mailbox; the settings ask for
  your company and postal address, which are appended to the opt-out footer when set.
* A clear opt-out on every outreach email (footer + `List-Unsubscribe` header; one-click links when
  a public HTTPS URL is configured). Opt-outs are applied immediately and permanently.
* Suppression checked at import, at review/approval, and immediately before every send.
* Review before activation: eligible/excluded counts, missing personalization, limits, schedule.
* Conservative defaults: dry-run on, low limits, spacing between messages, follow-ups stop on any
  human reply.
* No tracking pixels, no link rewriting, no hidden content.
* AI drafts are restricted to facts you provide and flagged for unverifiable numbers.

## What it deliberately does not do

No inbox rotation to evade limits, no spoofing, no spam-filter evasion, no scraping of private data
or bypassing website restrictions, no CAPTCHA solving, no purchased-list enrichment.

## Good practice

* Contact people only where there is a genuine, specific reason your offer is relevant to their role.
* Keep sequences short (2–3 follow-ups at most) and stop when asked.
* Keep lawful-basis records for where each lead came from (the `source` field and import audit help).
* Honor data-subject requests: delete the lead (the suppression entry remains so they are not
  re-added) and export what you hold with Leads → Export.
* Authenticate your domain (SPF, DKIM, DMARC). No setting guarantees inbox placement.
