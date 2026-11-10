"""Leagues, membership, teams, seasons, games, and players (R8-R14).

Every league-owned model names the lookup path to its league in
``league_lookup``; leagues.access uses it to scope every fetch (KTD3).
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from stats.catalog import POSITIONS

POSITION_CHOICES = [(position, position) for position in POSITIONS]


class ValidatedModel(models.Model):
    """Runs full validation (fields, clean(), uniqueness, constraints) on every save."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


def normalize_height(height):
    """Turn curly quotes into plain ones, so 6’2” becomes 6'2" (R26)."""
    return (
        height.replace("’", "'")
        .replace("‘", "'")
        .replace("”", '"')
        .replace("“", '"')
        .strip()
    )


class League(ValidatedModel):
    league_lookup = "pk"

    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Team(ValidatedModel):
    league_lookup = "league"

    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="teams")
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["league", "name"], name="unique_team_name_per_league")]

    def __str__(self):
        return self.name


class Season(ValidatedModel):
    league_lookup = "team__league"

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="seasons")
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ["team", "id"]
        constraints = [models.UniqueConstraint(fields=["team", "name"], name="unique_season_name_per_team")]

    def __str__(self):
        return self.name

    @property
    def league(self):
        return self.team.league


class Game(ValidatedModel):
    league_lookup = "season__team__league"

    class Result(models.TextChoices):
        WIN = "W", "Win"
        LOSS = "L", "Loss"
        TIE = "T", "Tie"

    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="games")
    order = models.PositiveSmallIntegerField("week")
    opponent = models.CharField(max_length=100)
    team_score = models.PositiveSmallIntegerField(null=True, blank=True)
    opponent_score = models.PositiveSmallIntegerField(null=True, blank=True)
    # Used only when a score is unknown (R14); otherwise the result comes from the scores.
    recorded_result = models.CharField(max_length=1, choices=Result.choices, blank=True)
    is_playoff = models.BooleanField(default=False)

    class Meta:
        ordering = ["season", "order", "id"]

    def __str__(self):
        return f"Week {self.order} vs {self.opponent}"

    @property
    def has_scores(self):
        return self.team_score is not None and self.opponent_score is not None

    @property
    def result(self):
        if self.has_scores:
            if self.team_score > self.opponent_score:
                return self.Result.WIN
            if self.team_score < self.opponent_score:
                return self.Result.LOSS
            return self.Result.TIE
        return self.recorded_result

    @property
    def league(self):
        return self.season.team.league

    def clean(self):
        if not self.has_scores and not self.recorded_result:
            raise ValidationError(
                {"recorded_result": "Enter both scores, or record the result directly."}
            )


class Player(ValidatedModel):
    league_lookup = "league"

    class Status(models.TextChoices):
        ACTIVE = "Active", "Active"
        RETIRED = "Retired", "Retired"

    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="players")
    team = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True, blank=True, related_name="players")
    name = models.CharField(max_length=100)
    overall = models.PositiveSmallIntegerField(null=True, blank=True)
    primary_position = models.CharField(max_length=4, choices=POSITION_CHOICES)
    secondary_position = models.CharField(max_length=4, choices=POSITION_CHOICES, blank=True)
    height = models.CharField(max_length=20, blank=True)
    weight = models.PositiveSmallIntegerField(null=True, blank=True)
    super_bowl_wins = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=7, choices=Status.choices, default=Status.ACTIVE)
    retirement_year = models.PositiveSmallIntegerField(null=True, blank=True)
    awards = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        self.height = normalize_height(self.height)
        if not isinstance(self.awards, list) or not all(isinstance(a, str) for a in self.awards):
            raise ValidationError({"awards": "Awards must be a list of names."})
        if self.team_id is not None and self.team.league_id != self.league_id:
            raise ValidationError({"team": "That team is not in this league."})


class Membership(ValidatedModel):
    league_lookup = "league"

    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    is_commissioner = models.BooleanField(default=False)
    # One player per member (this field), and one member per player (constraint below) (R10).
    player = models.ForeignKey(
        Player, on_delete=models.SET_NULL, null=True, blank=True, related_name="memberships"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["league", "user"], name="unique_membership_per_league"),
            models.UniqueConstraint(
                fields=["player"], condition=Q(player__isnull=False), name="one_member_per_player"
            ),
        ]

    def __str__(self):
        return f"{self.user} in {self.league}"

    def clean(self):
        if self.player_id is not None and self.player.league_id != self.league_id:
            raise ValidationError({"player": "That player is not in this league."})
