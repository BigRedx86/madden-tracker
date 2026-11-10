from django.conf import settings
from django.db import models

from leagues.models import League, ValidatedModel


class ImportDraft(ValidatedModel):
    """A legacy import waiting for the commissioner's review (U8, KTD7).

    ``proposal`` is importer.align.propose()'s output; ``resolutions`` maps
    conflict id to the commissioner's choice. Nothing else is saved until the
    commissioner confirms. One draft per league; a new upload replaces it.
    """

    league_lookup = "league"

    league = models.OneToOneField(League, on_delete=models.CASCADE, related_name="import_draft")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    file_name = models.CharField(max_length=255, blank=True)
    proposal = models.JSONField()
    resolutions = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"Import draft for {self.league}"
