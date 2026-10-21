"""Background worker entry point:  python -m app.workers.run"""

from __future__ import annotations

import logging
import signal
import time

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.base import utcnow
from app.db.session import SessionLocal
from app.workers.jobs import WORKER_ID, next_wakeup_hint, run_once

log = logging.getLogger("app.worker")
_stop = False


def _handle_signal(signum, _frame):  # type: ignore[no-untyped-def]
    global _stop
    log.info("Signal %s received; finishing current job then exiting", signum)
    _stop = True


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    log.info("Worker %s started (poll every %ss)", WORKER_ID, settings.worker_poll_seconds)
    while not _stop:
        try:
            outcomes = run_once()
            if outcomes:
                log.info("Processed %d job(s): %s", len(outcomes), ", ".join(outcomes))
                continue  # more work may be due
        except Exception:  # noqa: BLE001
            log.exception("Worker tick failed")
        db = SessionLocal()
        try:
            wait = next_wakeup_hint(db, utcnow()).total_seconds()
        finally:
            db.close()
        slept = 0.0
        while slept < wait and not _stop:
            time.sleep(0.5)
            slept += 0.5
    log.info("Worker stopped")


if __name__ == "__main__":
    main()
