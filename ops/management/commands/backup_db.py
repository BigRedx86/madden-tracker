"""Nightly backup, run by Unraid User Scripts: docker exec madden-tracker python manage.py backup_db"""

import logging

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from ops import backups

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Write a checked, gzipped snapshot of the database to BACKUP_DIR and keep the 14 newest."

    def handle(self, *args, **options):
        try:
            connection.ensure_connection()
            path = backups.run_backup(connection.connection, settings.BACKUP_DIR)
        except Exception as error:
            logger.exception("Backup failed")
            backups.record_failure(error)
            raise CommandError(f"Backup failed: {error}") from error
        backups.clear_failure()
        self.stdout.write(f"Backup written: {path}")
