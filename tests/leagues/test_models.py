"""Domain models (R8, R10, R12-R16)."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from leagues.models import Game, League, Membership, Player, Season, Team
from stats.models import StatLine


@pytest.fixture
def setup(make_user, make_league):
    commissioner = make_user("comm")
    return make_league("Lakeside", commissioner)


def make_game(season, **fields):
    return Game.objects.create(season=season, order=fields.pop("order", 2), opponent="Owls", **fields)


@pytest.mark.parametrize(
    "team_score, opponent_score, expected",
    [(24, 14, "W"), (10, 35, "L"), (21, 21, "T")],
)
def test_result_derives_from_scores(setup, team_score, opponent_score, expected):
    game = make_game(setup["season"], team_score=team_score, opponent_score=opponent_score)
    assert game.result == expected


def test_scores_win_over_a_stale_recorded_result(setup):
    game = make_game(setup["season"], team_score=24, opponent_score=14, recorded_result="L")
    assert game.result == "W"


def test_missing_score_uses_recorded_result(setup):
    game = make_game(setup["season"], team_score=None, opponent_score=None, recorded_result="W")
    assert game.result == "W"
    game = make_game(setup["season"], order=3, team_score=17, opponent_score=None, recorded_result="L")
    assert game.result == "L"


def test_missing_score_without_recorded_result_fails_validation(setup):
    with pytest.raises(ValidationError):
        make_game(setup["season"], team_score=None, opponent_score=None)
    with pytest.raises(ValidationError):
        make_game(setup["season"], team_score=20, opponent_score=None)


def test_second_stat_line_for_same_player_and_game_is_rejected(setup):
    player, game = setup["players"]["Alpha"], setup["game"]
    StatLine.objects.create(player=player, game=game, values={"receptions": 3})
    with pytest.raises((ValidationError, IntegrityError)):
        StatLine.objects.create(player=player, game=game, values={"receptions": 4})


def test_stat_line_validates_values_against_catalog_on_save(setup):
    player, game = setup["players"]["Alpha"], setup["game"]
    with pytest.raises(ValidationError):
        StatLine.objects.create(player=player, game=game, values={"warp_speed": 1})
    with pytest.raises(ValidationError):
        StatLine.objects.create(player=player, game=game, values={"receptions": -1})
    with pytest.raises(ValidationError):
        StatLine.objects.create(player=player, game=game, values={"sacks": 0.3})


def test_stat_line_stores_typed_values_and_drops_blanks(setup):
    player, game = setup["players"]["Alpha"], setup["game"]
    line = StatLine.objects.create(
        player=player, game=game, values={"rushing_yards": "-3", "sacks": "0.5", "receptions": ""}
    )
    line.refresh_from_db()
    assert line.values == {"rushing_yards": -3, "sacks": 0.5}


def test_dnp_stat_line_with_values_fails_validation(setup):
    player, game = setup["players"]["Alpha"], setup["game"]
    with pytest.raises(ValidationError):
        StatLine.objects.create(player=player, game=game, did_not_play=True, values={"receptions": 2})
    line = StatLine.objects.create(player=player, game=game, did_not_play=True)
    assert line.values == {}


def test_stat_line_player_and_game_must_share_a_league(setup, make_user, make_league):
    other = make_league("Hilltop", make_user("other-comm"))
    with pytest.raises(ValidationError):
        StatLine.objects.create(player=other["players"]["Alpha"], game=setup["game"])


def test_membership_is_unique_per_league_and_user(setup, make_user):
    user = make_user("member")
    Membership.objects.create(league=setup["league"], user=user)
    with pytest.raises((ValidationError, IntegrityError)):
        Membership.objects.create(league=setup["league"], user=user)


def test_two_members_linked_to_same_player_rejected(setup, make_user):
    alpha = setup["players"]["Alpha"]
    Membership.objects.create(league=setup["league"], user=make_user("m1"), player=alpha)
    with pytest.raises((ValidationError, IntegrityError)):
        Membership.objects.create(league=setup["league"], user=make_user("m2"), player=alpha)


def test_one_member_linked_to_two_players_rejected(setup, make_user):
    user = make_user("m1")
    Membership.objects.create(league=setup["league"], user=user, player=setup["players"]["Alpha"])
    with pytest.raises((ValidationError, IntegrityError)):
        Membership.objects.create(league=setup["league"], user=user, player=setup["players"]["Bravo"])


def test_unlinked_members_do_not_collide(setup, make_user):
    Membership.objects.create(league=setup["league"], user=make_user("m1"))
    Membership.objects.create(league=setup["league"], user=make_user("m2"))
    assert Membership.objects.filter(league=setup["league"], player__isnull=True).count() == 3


def test_member_cannot_link_to_another_leagues_player(setup, make_user, make_league):
    other = make_league("Hilltop", make_user("other-comm"))
    with pytest.raises(ValidationError):
        Membership.objects.create(league=setup["league"], user=make_user("m1"), player=other["players"]["Alpha"])


def test_player_holds_legacy_fields(setup):
    player = Player.objects.create(
        league=setup["league"],
        team=setup["team"],
        name="Charlie",
        overall=84,
        primary_position="WR",
        secondary_position="CB",
        height="6’2”",
        weight=205,
        super_bowl_wins=1,
        status=Player.Status.RETIRED,
        retirement_year=2028,
        awards=["MVP", "OPOY"],
    )
    player.refresh_from_db()
    assert player.height == "6'2\""
    assert player.weight == 205
    assert player.awards == ["MVP", "OPOY"]
    assert player.status == "Retired"


def test_player_awards_must_be_a_list_of_text(setup):
    with pytest.raises(ValidationError):
        Player.objects.create(league=setup["league"], name="Delta", primary_position="QB", awards="MVP")


def test_player_team_must_belong_to_the_players_league(setup, make_user, make_league):
    other = make_league("Hilltop", make_user("other-comm"))
    with pytest.raises(ValidationError):
        Player.objects.create(league=setup["league"], team=other["team"], name="Echo", primary_position="QB")


def test_league_contains_teams_seasons_games_players(setup):
    league = setup["league"]
    assert list(league.teams.all()) == [setup["team"]]
    assert list(setup["team"].seasons.all()) == [setup["season"]]
    assert list(setup["season"].games.all()) == [setup["game"]]
    assert league.players.count() == 2
    assert isinstance(league, League) and isinstance(setup["team"], Team) and isinstance(setup["season"], Season)
