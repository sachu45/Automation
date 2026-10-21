"""Back up the local SQLite database and key files into ./backups/<timestamp>/.

Uses SQLite's online backup API, so it is safe even while the app is running, but
stopping the API and worker first gives the most consistent snapshot.

    backend/.venv/Scripts/python scripts/backup.py        (Windows)
    backend/.venv/bin/python scripts/backup.py            (macOS/Linux)

For PostgreSQL deployments use pg_dump instead (see docs/OPERATIONS.md).
Backups contain personal data and secrets — store them encrypted.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "backend" / "data"


def main() -> int:
    db = DATA / "outreach.db"
    if not db.exists():
        print(f"No database at {db}", file=sys.stderr)
        return 1
    dest = ROOT / "backups" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(db)
    out = sqlite3.connect(dest / "outreach.db")
    with out:
        src.backup(out)
    src.close()
    out.close()
    for key in ("encryption.key", "secret.key"):
        if (DATA / key).exists():
            shutil.copy2(DATA / key, dest / key)
    print(f"Backup written to {dest}")
    print("Keep encryption.key with the database: without it stored account credentials cannot be decrypted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
