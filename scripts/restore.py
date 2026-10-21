"""Restore a backup created by scripts/backup.py. Stop the API and worker first.

    backend/.venv/Scripts/python scripts/restore.py backups/20261019T120000Z

The current database is moved aside (outreach.db.before-restore-<ts>), never deleted.
After restoring, run `alembic upgrade head` in backend/ in case the backup predates
newer migrations.
"""

from __future__ import annotations

import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "backend" / "data"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    src = Path(argv[1]).resolve()
    if not (src / "outreach.db").exists():
        print(f"{src} does not contain outreach.db", file=sys.stderr)
        return 1
    DATA.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    for name in ("outreach.db", "encryption.key", "secret.key"):
        cur = DATA / name
        if cur.exists():
            cur.rename(DATA / f"{name}.before-restore-{stamp}")
        if (src / name).exists():
            shutil.copy2(src / name, cur)
    for suffix in ("-wal", "-shm"):
        stale = DATA / f"outreach.db{suffix}"
        if stale.exists():
            stale.rename(DATA / f"outreach.db{suffix}.before-restore-{stamp}")
    print(f"Restored from {src}. Previous files kept with suffix .before-restore-{stamp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
