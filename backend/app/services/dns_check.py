"""Best-effort SPF / DKIM / DMARC lookups for a sending domain.

Limits: these checks only confirm that records *exist* and look syntactically plausible.
They cannot verify that your provider actually signs with the given DKIM selector, that
SPF includes the IPs your provider really uses, or how receivers will evaluate them.
"""

from __future__ import annotations

from typing import Any

import dns.exception
import dns.resolver


def _txt(name: str) -> list[str]:
    try:
        answers = dns.resolver.resolve(name, "TXT", lifetime=5)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except (dns.exception.DNSException, OSError) as exc:
        raise RuntimeError(f"DNS lookup failed for {name}: {type(exc).__name__}") from exc
    return [b"".join(r.strings).decode(errors="replace") for r in answers]  # type: ignore[attr-defined]


def check_domain(domain: str, dkim_selector: str | None = None) -> dict[str, Any]:
    domain = domain.strip().lower()
    result: dict[str, Any] = {"domain": domain, "spf": None, "dmarc": None, "dkim": None, "notes": []}
    try:
        spf = [t for t in _txt(domain) if t.lower().startswith("v=spf1")]
        result["spf"] = {
            "found": bool(spf),
            "records": spf,
            "ok": len(spf) == 1,
            "message": "One SPF record found" if len(spf) == 1 else ("Multiple SPF records (invalid)" if spf else "No SPF record"),
        }
        dmarc = [t for t in _txt(f"_dmarc.{domain}") if t.lower().startswith("v=dmarc1")]
        policy = None
        if dmarc:
            for part in dmarc[0].split(";"):
                k, _, v = part.strip().partition("=")
                if k.lower() == "p":
                    policy = v.strip()
        result["dmarc"] = {"found": bool(dmarc), "records": dmarc, "policy": policy, "ok": bool(dmarc)}
        if dkim_selector:
            dk = _txt(f"{dkim_selector}._domainkey.{domain}")
            result["dkim"] = {
                "selector": dkim_selector,
                "found": any("p=" in t for t in dk),
                "records": [t[:120] + "…" if len(t) > 120 else t for t in dk],
            }
        else:
            result["notes"].append("Provide your provider's DKIM selector (e.g. 'google', 'selector1') to check DKIM.")
    except RuntimeError as exc:
        result["error"] = str(exc)
    result["notes"].append(
        "These checks confirm records exist; they cannot prove your provider signs mail correctly or guarantee deliverability."
    )
    return result
