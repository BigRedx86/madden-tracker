"""Shared fixtures. All names are fake (never real league members)."""

import pytest
from django.contrib.auth import get_user_model

from leagues.models import Game, League, Membership, Player, Season, Team


@pytest.fixture
def make_user(db):
    def make(username, **extra):
        return get_user_model().objects.create_user(username=username, password="pw-for-tests-only", **extra)

    return make


@pytest.fixture
def make_league(db):
    """Build a league with one team, one season, one game, and the given players."""

    def make(name, commissioner, members=(), players=("Alpha", "Bravo")):
        league = League.objects.create(name=name)
        team = Team.objects.create(league=league, name=f"{name} Team")
        season = Season.objects.create(team=team, name="01-02")
        game = Game.objects.create(season=season, order=1, opponent="Sharks", team_score=24, opponent_score=14)
        made = {
            player_name: Player.objects.create(
                league=league, team=team, name=player_name, primary_position="WR"
            )
            for player_name in players
        }
        Membership.objects.create(league=league, user=commissioner, is_commissioner=True)
        for user, player_name in members:
            Membership.objects.create(league=league, user=user, player=made.get(player_name))
        return {"league": league, "team": team, "season": season, "game": game, "players": made}

    return make
