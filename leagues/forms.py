from django import forms

from .models import Game, League, Player, Season, Team


class LeagueForm(forms.ModelForm):
    class Meta:
        model = League
        fields = ["name"]
        labels = {"name": "League name"}


class TeamForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ["name"]
        labels = {"name": "Team name"}
        help_texts = {"name": "For example, Dolphins."}

    def __init__(self, *args, league, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.league = league

    def clean_name(self):
        # The (league, name) constraint involves a field not on the form, so
        # the form wouldn't check it on its own.
        name = self.cleaned_data["name"].strip()
        duplicates = Team.objects.filter(league=self.instance.league, name__iexact=name).exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise forms.ValidationError("This league already has a team with that name.")
        return name


class SeasonForm(forms.ModelForm):
    class Meta:
        model = Season
        fields = ["team", "name"]
        labels = {"name": "Season name"}
        help_texts = {"name": "For example, 26-27."}

    def __init__(self, *args, league, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["team"].queryset = league.teams.all()
        self.fields["team"].empty_label = None


class PlayerForm(forms.ModelForm):
    """Every player field (R12): the commissioner's form."""

    awards = forms.CharField(
        required=False,
        max_length=500,
        help_text="Separate awards with commas, for example: MVP, OPOY.",
    )

    class Meta:
        model = Player
        fields = [
            "name",
            "team",
            "overall",
            "primary_position",
            "secondary_position",
            "height",
            "weight",
            "super_bowl_wins",
            "status",
            "retirement_year",
            "awards",
        ]
        labels = {"super_bowl_wins": "Super Bowl wins"}
        help_texts = {"height": "For example, 6'2\"."}

    def __init__(self, *args, league, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.league = league
        if "team" in self.fields:
            self.fields["team"].queryset = league.teams.all()
        self.fields["secondary_position"].choices = [("", "None")] + list(
            self.fields["secondary_position"].choices
        )[1:]
        if "awards" in self.fields:
            self.initial["awards"] = ", ".join(self.instance.awards or [])
        for name in ("overall", "weight", "super_bowl_wins", "retirement_year"):
            if name in self.fields:
                self.fields[name].widget.attrs.update(inputmode="numeric")

    def clean_awards(self):
        return [award.strip() for award in self.cleaned_data["awards"].split(",") if award.strip()]


class BioForm(PlayerForm):
    """The fields a member may edit on their own player (R11).

    Team, Super Bowl wins, status, retirement, and awards stay with the commissioner.
    """

    awards = None

    class Meta(PlayerForm.Meta):
        fields = ["name", "overall", "primary_position", "secondary_position", "height", "weight"]


class GameForm(forms.ModelForm):
    class Meta:
        model = Game
        fields = ["order", "opponent", "team_score", "opponent_score", "recorded_result", "is_playoff"]
        labels = {
            "team_score": "Our score",
            "opponent_score": "Their score",
            "recorded_result": "Result, only if a score is unknown",
            "is_playoff": "Playoff game",
        }
        help_texts = {
            "team_score": "Leave blank if unknown.",
            "opponent_score": "Leave blank if unknown.",
            "recorded_result": (
                "With both scores entered, the result comes from the scores and this is ignored. "
                "If either score is blank, pick W, L, or T here."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["recorded_result"].choices = [("", "From the scores")] + list(Game.Result.choices)
        for name in ("order", "team_score", "opponent_score"):
            self.fields[name].widget.attrs.update(inputmode="numeric")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("team_score") is not None and cleaned.get("opponent_score") is not None:
            # The scores decide; don't keep a stale manual result around.
            cleaned["recorded_result"] = ""
        return cleaned


class LinkPlayerForm(forms.Form):
    player = forms.ModelChoiceField(queryset=Player.objects.none(), label="Player")

    def __init__(self, *args, league, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["player"].queryset = league.players.all()
