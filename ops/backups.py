"""Live SQLite snapshots and their status (R28, KTD8).

A backup is a gzipped copy made with SQLite's online backup API, so it is
consistent even while the app is writing. Each one is checked with
``PRAGMA integrity_check`` before it is kept, and only the newest ``KEEP``
are kept.

Status comes from the files themselves: the newest snapshot's timestamp (in its
name) is the last success, so a backup never has to write to the live database.
A failed run writes a small JSON file to DATA_DIR (not BACKUP_DIR, which may be
the thing that's broken); the next success removes it.
"""

import gzip
import json
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

KEEP = 14
STALE_AFTER = timedelta(hours=26)
NAME_FORMAT = "madden-%Y%m%d-%H%M%SZ.sqlite3.gz"
NAME_PATTERN = re.compile(r"^madden-\d{8}-\d{6}Z\.sqlite3\.gz$")
FAILURE_FILE = "backup_failure.json"


class BackupError(Exception):
    pass


def backup_name(when):
    return when.astimezone(UTC).strftime(NAME_FORMAT)


def snapshots(backup_dir):
    """Snapshot paths in ``backup_dir``, oldest first (names sort by time)."""
    try:
        names = os.listdir(backup_dir)
    except OSError:
        return []
    return [Path(backup_dir) / name for name in sorted(names) if NAME_PATTERN.match(name)]


def run_backup(source, backup_dir, now=None):
    """Write a checked, gzipped snapshot of the open sqlite3 connection ``source``.

    Returns the new file's path. On any error, raises and leaves no partial
    file; existing snapshots are untouched.
    """
    backup_dir = Path(backup_dir)
    final = backup_dir / backup_name(now or timezone.now())
    temps = []
    try:
        # mkstemp creates files readable only by the owner (0600), and
        # os.replace keeps that mode: backups hold password hashes and 2FA secrets.
        raw_fd, raw_path = tempfile.mkstemp(dir=backup_dir, prefix=".backup-", suffix=".sqlite3")
        os.close(raw_fd)
        temps.append(raw_path)
        copy = sqlite3.connect(raw_path)
        try:
            source.backup(copy)
            result = copy.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            copy.close()
        if result != "ok":
            raise BackupError(f"integrity check failed: {result}")

        gz_fd, gz_path = tempfile.mkstemp(dir=backup_dir, prefix=".backup-", suffix=".gz")
        temps.append(gz_path)
        with os.fdopen(gz_fd, "wb") as out, open(raw_path, "rb") as raw:
            with gzip.GzipFile(fileobj=out, mode="wb") as zipped:
                shutil.copyfileobj(raw, zipped)
            out.flush()
            os.fsync(out.fileno())
        os.replace(gz_path, final)
        temps.remove(gz_path)
    finally:
        for path in temps:
            try:
                os.remove(path)
            except OSError:
                pass

    for old in snapshots(backup_dir)[:-KEEP]:
        old.unlink()
    return final


def _failure_path():
    return Path(settings.DATA_DIR) / FAILURE_FILE


def record_failure(error):
    data = {"at": timezone.now().isoformat(), "error": str(error)}
    _failure_path().write_text(json.dumps(data))


def clear_failure():
    _failure_path().unlink(missing_ok=True)


def last_failure():
    try:
        data = json.loads(_failure_path().read_text())
    except (OSError, ValueError):
        return None
    return {"at": datetime.fromisoformat(data["at"]), "error": data["error"]}


def status(now=None):
    """What the site-admin page shows about backups."""
    now = now or timezone.now()
    backup_dir = Path(settings.BACKUP_DIR)
    files = snapshots(backup_dir)
    last_success = None
    if files:
        last_success = datetime.strptime(files[-1].name, NAME_FORMAT).replace(tzinfo=UTC)
    return {
        "dir": backup_dir,
        "count": len(files),
        "last_success": last_success,
        "is_stale": last_success is None or now - last_success > STALE_AFTER,
        "last_failure": last_failure(),
    }
