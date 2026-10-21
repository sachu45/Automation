"""Local SMTP sink for development: accepts mail on 127.0.0.1:1025 and stores it as .eml files.

Nothing is relayed anywhere. Use it to exercise LIVE sending end-to-end without
contacting real recipients: create a sending account with host 127.0.0.1, port 1025,
security "none", any username/password.

    backend/.venv/Scripts/python scripts/dev_smtp.py      (Windows)
    backend/.venv/bin/python scripts/dev_smtp.py          (macOS/Linux)
"""

from __future__ import annotations

import time
from pathlib import Path

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult

OUT = Path(__file__).resolve().parents[1] / "backend" / "data" / "dev-mailbox"


class Sink:
    async def handle_DATA(self, server, session, envelope):  # noqa: N802
        OUT.mkdir(parents=True, exist_ok=True)
        name = OUT / f"{int(time.time() * 1000)}.eml"
        name.write_bytes(envelope.original_content or envelope.content)
        print(f"[dev-smtp] {envelope.mail_from} -> {', '.join(envelope.rcpt_tos)} saved {name.name}", flush=True)
        return "250 Message accepted for delivery (dev sink, not relayed)"


def _accept_any(server, session, envelope, mechanism, auth_data):  # noqa: ANN001
    return AuthResult(success=True)


if __name__ == "__main__":
    ctl = Controller(Sink(), hostname="127.0.0.1", port=1025, authenticator=_accept_any, auth_require_tls=False)
    ctl.start()
    print(f"[dev-smtp] listening on 127.0.0.1:1025, writing to {OUT}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        ctl.stop()
