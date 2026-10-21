# Email providers: what is supported and what can be known

| Capability | Generic SMTP/IMAP | Gmail (OAuth, SMTP/IMAP XOAUTH2) | Microsoft 365 (OAuth, SMTP/IMAP XOAUTH2) |
|---|---|---|---|
| Send | ✅ | ✅ (implemented, mock-tested only) | ✅ (implemented, mock-tested only; SMTP AUTH must be enabled for the mailbox) |
| Connection test | ✅ real SMTP login + NOOP, IMAP login + SELECT | ✅ | ✅ |
| SMTP acceptance (250) | ✅ recorded as `accepted` | ✅ | ✅ |
| Confirmed delivery | ❌ not reported by SMTP | ❌ | ❌ |
| Immediate rejection (5xx) | ✅ → `failed_permanent`, or `bounced` + suppression for mailbox-unknown codes (5.1.x etc.) | ✅ | ✅ |
| Asynchronous bounces | ✅ via DSN messages in the synced folder (RFC 3464). Non-standard bounces → `bounce_uncertain`, no suppression | ✅ | ✅ |
| Replies | ✅ IMAP, matched by headers / sender | ✅ | ✅ |
| Out-of-office detection | Heuristic (Auto-Submitted, X-Autoreply, Precedence, subject/body patterns) | same | same |
| Webhook delivery events | ❌ none implemented | ❌ | ❌ |
| Opens/clicks | Not tracked by design | — | — |

## OAuth scopes

* **Google**: `https://mail.google.com/` — the only scope Google accepts for SMTP/IMAP XOAUTH2.
  It is broad (full mailbox). The app only sends approved messages and reads the configured folder
  read-only. Register a "Web application" OAuth client; redirect URI
  `<OAUTH_REDIRECT_BASE_URL>/api/accounts/oauth/callback`.
* **Microsoft**: `https://outlook.office.com/SMTP.Send`, `https://outlook.office.com/IMAP.AccessAsUser.All`,
  `offline_access`. Register an app in Entra ID with the same redirect URI.

Tokens are stored encrypted; refresh failures (revoked/expired consent) set the account to
`auth_failed`, disable sending and pause dependent campaigns.

## App passwords

For Google Workspace / Gmail with 2-Step Verification, an *app password* with SMTP
(`smtp.gmail.com:465 SSL` or `:587 STARTTLS`) and IMAP (`imap.gmail.com:993 SSL`) works with the
generic adapter. Many hosts (Fastmail, Zoho, Migadu, cPanel hosts) work the same way.
