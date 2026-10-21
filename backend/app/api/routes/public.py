"""Public, unauthenticated opt-out endpoints. No personal data is shown or accepted in URLs."""

from __future__ import annotations

import html

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.api.deps import db_session
from app.core import ratelimit
from app.services import unsubscribe
from app.services.settings import get_config

router = APIRouter(tags=["public"])

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{title}</title>
<style>body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#f8fafc;color:#0f172a;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;padding:16px}}
main{{background:#fff;border:1px solid #e2e8f0;border-radius:12px;max-width:440px;width:100%;padding:28px}}h1{{font-size:20px;margin:0 0 8px}}p{{color:#475569;line-height:1.5}}
button{{background:#0f172a;color:#fff;border:0;border-radius:8px;padding:10px 16px;font-size:15px;cursor:pointer}}</style></head>
<body><main>{body}</main></body></html>"""


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(PAGE.format(title=html.escape(title), body=body), status_code=status, headers={"Cache-Control": "no-store"})


@router.get("/u/{token}", response_class=HTMLResponse)
def unsubscribe_page(token: str, request: Request, db: Session = Depends(db_session)) -> HTMLResponse:
    ratelimit.enforce(request, "unsub_get", 30, 60)
    if unsubscribe.read_token(token) is None:
        return _page(
            "Link not valid",
            "<h1>This link is not valid</h1><p>It may be incomplete. You can also reply to the email with “unsubscribe”.</p>",
            404,
        )
    sender = html.escape(get_config(db).company_name or get_config(db).sender_name or "this sender")
    # GET never changes state (mail scanners prefetch links); the user confirms with a POST.
    return _page(
        "Unsubscribe",
        f"<h1>Unsubscribe</h1><p>Stop receiving emails from {sender}?</p>"
        f'<form method="post" action="/u/{html.escape(token)}"><button type="submit">Yes, unsubscribe me</button></form>',
    )


@router.post("/u/{token}", response_class=HTMLResponse)
async def unsubscribe_confirm(token: str, request: Request, db: Session = Depends(db_session)):  # type: ignore[no-untyped-def]
    ratelimit.enforce(request, "unsub_post", 10, 60)
    body = (await request.body())[:2000].decode(errors="ignore")
    one_click = "List-Unsubscribe=One-Click" in body
    ok = unsubscribe.process(db, token, "one_click" if one_click else "unsubscribe_link")
    db.commit()
    if one_click:  # RFC 8058 clients expect a simple 2xx
        return PlainTextResponse("ok" if ok else "invalid", status_code=200 if ok else 404)
    if not ok:
        return _page("Link not valid", "<h1>This link is not valid</h1><p>Please reply to the email with “unsubscribe” instead.</p>", 404)
    return _page(
        "Unsubscribed", "<h1>You're unsubscribed</h1><p>You won't receive further emails from this sender. Sorry for the interruption.</p>"
    )
