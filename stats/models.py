from django.core.exceptions import ValidationError
from django.db import models

from leagues.models import Game, Player, ValidatedModel

from . import catalog


class StatLine(ValidatedModel):
    """One player's stats for one game, or a "did not play" marker (R15, R16).

    ``values`` maps catalog keys to values and is validated against the catalog
    on every save. Blank inputs are absent keys, not 0 (KTD6).
    """

    league_lookup = "player__league"

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="stat_lines")
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="stat_lines")
    did_not_play = models.BooleanField(default=False)
    values = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["player", "game"], name="one_stat_line_per_player_game")]

    def __str__(self):
        return f"{self.player} - {self.game}"

    def clean(self):
        self.values = catalog.normalize_values(self.values)
        if self.did_not_play and self.values:
            raise ValidationError({"values": "A did-not-play line can't have stats."})
        if self.player_id and self.game_id and self.player.league_id != self.game.season.team.league_id:
            raise ValidationError("The player and the game must be in the same league.")
