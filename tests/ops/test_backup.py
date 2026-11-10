"""Scheduled backups and the site-admin backup status (R28, KTD8)."""

import gzip
import os
import shutil
import sqlite3
import stat
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.utils import timezone
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice

from leagues.models import Game, League, Membership, Player, Season, Team
from ops import backups
from stats.models import StatLine

TABLES = [
    "auth_user",
    "leagues_league",
    "leagues_team",
    "leagues_season",
    "leagues_game",
    "leagues_player",
    "leagues_membership",
    "stats_statline",
]


@pytest.fixture
def backup_dir(transactional_db, tmp_path, settings):
    # transactional_db, not db: the command backs up through Django's own
    # connection, and SQLite can't back up a connection that is holding an
    # open write transaction (which the db fixture's wrapping atomic would be).
    path = tmp_path / "backups"
    path.mkdir()
    settings.BACKUP_DIR = path
    settings.DATA_DIR = tmp_path / "data"
    settings.DATA_DIR.mkdir()
    return path


def backup_files(path):
    return sorted(p.name for p in Path(path).iterdir())


def restore(snapshot, target):
    """Gunzip a snapshot to ``target`` and open it, as the README's restore drill does."""
    with gzip.open(snapshot, "rb") as source, open(target, "wb") as out:
        shutil.copyfileobj(source, out)
    return sqlite3.connect(target)


def populated_league(make_user):
    commissioner = make_user("coach")
    member = make_user("jordan")
    league = League.objects.create(name="Backup League")
    team = Team.objects.create(league=league, name="Backup Team")
    season = Season.objects.create(team=team, name="01-02")
    player = Player.objects.create(league=league, team=team, name="Alpha", primary_position="WR")
    Membership.objects.create(league=league, user=commissioner, is_commissioner=True)
    Membership.objects.create(league=league, user=member, player=player)
    for order, yards in enumerate([88, -3, 140], start=1):
        game = Game.objects.create(season=season, order=order, opponent="Sharks", team_score=21, opponent_score=17)
        StatLine.objects.create(player=player, game=game, values={"receptions": 5, "receiving_yards": yards})
    return player


def live_counts():
    with connection.cursor() as cursor:
        counts = {}
        for table in TABLES:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            counts[table] = cursor.fetchone()[0]
    return counts


def test_backup_restores_identical_rows_and_player_totals(backup_dir, make_user, tmp_path):
    player = populated_league(make_user)
    expected_counts = live_counts()
    expected_yards = sum(line.values["receiving_yards"] for line in player.stat_lines.all())
    assert expected_yards == 225

    call_command("backup_db")

    [name] = backup_files(backup_dir)
    restored = restore(backup_dir / name, tmp_path / "restored.sqlite3")
    for table, count in expected_counts.items():
        assert restored.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == count, table
    yards = restored.execute(
        "SELECT SUM(json_extract(s.\"values\", '$.receiving_yards')) FROM stats_statline s "
        "JOIN leagues_player p ON p.id = s.player_id WHERE p.name = 'Alpha'"
    ).fetchone()[0]
    assert yards == expected_yards
    assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_backup_file_is_private(backup_dir):
    call_command("backup_db")
    [name] = backup_files(backup_dir)
    assert stat.S_IMODE((backup_dir / name).stat().st_mode) == 0o600


def test_retention_keeps_the_14_newest(backup_dir, tmp_path):
    source = sqlite3.connect(tmp_path / "live.sqlite3")
    source.execute("CREATE TABLE t (x)")
    source.commit()
    start = timezone.now() - timedelta(days=20)
    times = [start + timedelta(days=day) for day in range(16)]
    for when in times:
        backups.run_backup(source, backup_dir, now=when)

    names = backup_files(backup_dir)
    assert len(names) == 14
    assert names == sorted(backups.backup_name(when) for when in times[2:])


def test_backup_during_open_write_transaction_passes_integrity_check(backup_dir, tmp_path):
    live = tmp_path / "live.sqlite3"
    setup = sqlite3.connect(live)
    setup.execute("PRAGMA journal_mode=WAL")
    setup.execute("CREATE TABLE t (x)")
    setup.executemany("INSERT INTO t VALUES (?)", [(n,) for n in range(1000)])
    setup.commit()

    writer = sqlite3.connect(live, isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")
    writer.executemany("INSERT INTO t VALUES (?)", [(n,) for n in range(5000)])
    try:
        backups.run_backup(sqlite3.connect(live), backup_dir)
    finally:
        writer.execute("ROLLBACK")

    [name] = backup_files(backup_dir)
    restored = restore(backup_dir / name, tmp_path / "restored.sqlite3")
    assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert restored.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1000


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unwritable_backup_dir_fails_and_leaves_no_partial_file(backup_dir):
    call_command("backup_db")
    before = backup_files(backup_dir)
    backup_dir.chmod(0o500)
    try:
        with pytest.raises(CommandError):
            call_command("backup_db")
    finally:
        backup_dir.chmod(0o700)

    assert backup_files(backup_dir) == before
    failure = backups.last_failure()
    assert failure is not None
    assert "Permission denied" in failure["error"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_success_clears_a_recorded_failure(backup_dir):
    backup_dir.chmod(0o500)
    try:
        with pytest.raises(CommandError):
            call_command("backup_db")
    finally:
        backup_dir.chmod(0o700)
    assert backups.last_failure() is not None

    call_command("backup_db")
    assert backups.last_failure() is None


def test_failed_integrity_check_raises_and_keeps_nothing_new(backup_dir, tmp_path, monkeypatch):
    source = sqlite3.connect(tmp_path / "live.sqlite3")
    source.execute("CREATE TABLE t (x)")
    source.commit()
    existing = backups.run_backup(source, backup_dir, now=timezone.now() - timedelta(days=1))
    existing_bytes = existing.read_bytes()

    class CorruptCopy:
        """The backup copy, reporting a failed integrity check."""

        def __init__(self, real):
            self.real = real

        def execute(self, sql):
            if "integrity_check" in sql:
                return self.real.execute("SELECT '*** in database main *** Page 2: btree corrupt'")
            return self.real.execute(sql)

        def close(self):
            self.real.close()

    class Source:
        def backup(self, target):
            source.backup(target.real)

    real_connect = sqlite3.connect
    monkeypatch.setattr(backups, "sqlite3", SimpleNamespace(connect=lambda path: CorruptCopy(real_connect(path))))

    with pytest.raises(backups.BackupError, match="integrity check failed: .*btree corrupt"):
        backups.run_backup(Source(), backup_dir)

    # backup_files lists every entry, so this also rules out leftover .backup-* temps.
    assert backup_files(backup_dir) == [existing.name]
    assert existing.read_bytes() == existing_bytes


def test_restore_drill_ignores_stale_wal_and_shm(backup_dir, tmp_path):
    """README restore drill: stop, delete -wal and -shm, gunzip the snapshot over the db, start."""
    live = tmp_path / "db.sqlite3"
    conn = sqlite3.connect(live)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE t (x)")
    conn.executemany("INSERT INTO t VALUES (?)", [(n,) for n in range(100)])
    conn.commit()
    backups.run_backup(conn, backup_dir)
    snapshot_rows = conn.execute("SELECT x FROM t ORDER BY x").fetchall()

    # Changes after the snapshot that live only in the WAL, as after a crash.
    conn.execute("DELETE FROM t WHERE x < 50")
    conn.executemany("INSERT INTO t VALUES (?)", [(n,) for n in range(1000, 1100)])
    conn.commit()
    stopped = tmp_path / "stopped"
    stopped.mkdir()
    for suffix in ("", "-wal", "-shm"):
        shutil.copy(f"{live}{suffix}", stopped / f"db.sqlite3{suffix}")
    conn.close()
    assert (stopped / "db.sqlite3-wal").stat().st_size > 0

    for suffix in ("-wal", "-shm"):
        (stopped / f"db.sqlite3{suffix}").unlink()
    [name] = backup_files(backup_dir)
    restored = restore(backup_dir / name, stopped / "db.sqlite3")

    assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert restored.execute("SELECT x FROM t ORDER BY x").fetchall() == snapshot_rows


@pytest.fixture
def admin_client(client, make_user):
    admin = make_user("owner", is_superuser=True, is_staff=True)
    device = TOTPDevice.objects.create(user=admin, name="default", confirmed=True)
    client.force_login(admin)
    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    session.save()
    return client


def backup_section(client):
    page = client.get("/site-admin/").content.decode()
    start = page.index('id="backup-status"')
    return page[start : page.index("</section>", start)]


def test_site_admin_warns_when_last_backup_is_27_hours_old(admin_client, backup_dir, tmp_path):
    source = sqlite3.connect(tmp_path / "live.sqlite3")
    backups.run_backup(source, backup_dir, now=timezone.now() - timedelta(hours=27))
    section = backup_section(admin_client)
    assert "Last successful backup" in section
    assert "more than 26 hours old" in section


def test_site_admin_no_warning_when_last_backup_is_2_hours_old(admin_client, backup_dir, tmp_path):
    source = sqlite3.connect(tmp_path / "live.sqlite3")
    backups.run_backup(source, backup_dir, now=timezone.now() - timedelta(hours=2))
    section = backup_section(admin_client)
    assert "Last successful backup" in section
    assert "notice" not in section


def test_site_admin_warns_when_no_backup_exists(admin_client, backup_dir):
    section = backup_section(admin_client)
    assert "No backups" in section
    assert "notice" in section


def test_site_admin_shows_recorded_failure(admin_client, backup_dir, tmp_path):
    backups.run_backup(sqlite3.connect(tmp_path / "live.sqlite3"), backup_dir)
    backups.record_failure("disk full")
    section = backup_section(admin_client)
    assert "last backup attempt failed" in section
    assert "disk full" in section
