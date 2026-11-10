"""Game management, season pages, and game detail (U6: R14, R15, F1, AE2)."""

import pytest

from leagues.models import Game
from stats.models import StatLine


@pytest.fixture
def setup(make_user, make_league):
    commissioner = make_user("comm")
    jordan = make_user("jordan")
    corey = make_user("corey")
    built = make_league(
        "Lakeside",
        commissioner,
        members=[(jordan, "Jordan"), (corey, "Corey")],
        players=("Jordan", "Corey", "Casey"),
    )
    built.update(commissioner=commissioner, jordan=jordan, corey=corey)
    return built


def url(setup, path=""):
    return f"/league/{setup['league'].pk}/{path}"


GAME = {"order": "2", "opponent": "Owls", "team_score": "24", "opponent_score": "14", "recorded_result": ""}


def test_commissioner_adds_a_game(client, setup):
    client.force_login(setup["commissioner"])
    response = client.post(url(setup, f"seasons/{setup['season'].pk}/games/new/"), dict(GAME, is_playoff="on"))
    assert response.status_code == 302
    game = Game.objects.get(opponent="Owls")
    assert (game.season, game.order, game.result, game.is_playoff) == (setup["season"], 2, "W", True)


def test_new_game_form_suggests_the_next_week(client, setup):
    client.force_login(setup["commissioner"])
    page = client.get(url(setup, f"seasons/{setup['season'].pk}/games/new/")).content.decode()
    assert 'name="order" value="2"' in page


def test_game_with_a_missing_score_needs_a_recorded_result(client, setup):
    client.force_login(setup["commissioner"])
    new = url(setup, f"seasons/{setup['season'].pk}/games/new/")
    response = client.post(new, dict(GAME, opponent_score=""))
    assert response.status_code == 200
    assert "record the result" in response.content.decode()
    assert not Game.objects.filter(opponent="Owls").exists()

    client.post(new, dict(GAME, opponent_score="", recorded_result="L"))
    assert Game.objects.get(opponent="Owls").result == "L"


def test_editing_scores_from_24_14_to_14_24_changes_the_result_to_loss(client, setup):
    game = setup["game"]
    assert game.result == "W"
    client.force_login(setup["commissioner"])
    data = {"order": "1", "opponent": "Sharks", "team_score": "14", "opponent_score": "24", "recorded_result": ""}
    assert client.post(url(setup, f"games/{game.pk}/edit/"), data).status_code == 302
    game.refresh_from_db()
    assert game.result == "L"


def test_member_cannot_add_edit_move_or_delete_games(client, setup):
    game = setup["game"]
    client.force_login(setup["jordan"])
    assert client.post(url(setup, f"seasons/{setup['season'].pk}/games/new/"), GAME).status_code == 403
    assert client.post(url(setup, f"games/{game.pk}/edit/"), GAME).status_code == 403
    assert client.post(url(setup, f"games/{game.pk}/move/"), {"direction": "down"}).status_code == 403
    assert client.post(url(setup, f"games/{game.pk}/delete/")).status_code == 403
    assert Game.objects.filter(pk=game.pk, opponent="Sharks").exists()


def test_deleting_a_game_asks_first_then_deletes_its_stat_lines(client, setup):
    game = setup["game"]
    StatLine.objects.create(player=setup["players"]["Jordan"], game=game, values={"receptions": 4})
    client.force_login(setup["commissioner"])
    confirm = client.get(url(setup, f"games/{game.pk}/delete/"))
    assert confirm.status_code == 200
    assert "1 stat line" in confirm.content.decode()
    assert Game.objects.filter(pk=game.pk).exists()

    response = client.post(url(setup, f"games/{game.pk}/delete/"))
    assert response.status_code == 302
    assert not Game.objects.filter(pk=game.pk).exists()
    assert not StatLine.objects.exists()


def test_commissioner_reorders_two_games_and_the_order_persists(client, setup):
    first = setup["game"]
    second = Game.objects.create(season=setup["season"], order=2, opponent="Owls", team_score=3, opponent_score=7)
    client.force_login(setup["commissioner"])
    client.post(url(setup, f"games/{second.pk}/move/"), {"direction": "up"})
    assert list(setup["season"].games.values_list("opponent", flat=True)) == ["Owls", "Sharks"]
    page = client.get(url(setup, f"seasons/{setup['season'].pk}/")).content.decode()
    assert page.index("Owls") < page.index("Sharks")
    first.refresh_from_db()
    assert first.order == 2


def test_member_season_page_lists_only_existing_games_and_says_missing_ones_arent_added(client, setup):
    # Covers AE2.
    season = setup["season"]
    for week in range(2, 13):
        Game.objects.create(season=season, order=week, opponent=f"Team {week}", team_score=10, opponent_score=3)
    client.force_login(setup["jordan"])
    page = client.get(url(setup, f"seasons/{season.pk}/")).content.decode()
    assert "Week 12" in page
    assert "Week 13" not in page
    assert "hasn't been added yet" in page


def test_member_season_page_shows_entry_status_and_highlights_next(client, setup):
    season = setup["season"]
    second = Game.objects.create(season=season, order=2, opponent="Owls", team_score=3, opponent_score=7)
    StatLine.objects.create(player=setup["players"]["Jordan"], game=setup["game"], values={"receptions": 2})
    client.force_login(setup["jordan"])
    page = client.get(url(setup, f"seasons/{season.pk}/")).content.decode()
    assert "Entered" in page
    highlighted = page[page.index("game--next"):]
    highlighted = highlighted[: highlighted.index("</li>")]
    assert "Owls" in highlighted and "Next to enter" in highlighted
    assert f"/games/{second.pk}/stats/{setup['players']['Jordan'].pk}/" in page


def test_season_page_has_no_game_controls_for_members(client, setup):
    client.force_login(setup["jordan"])
    page = client.get(url(setup, f"seasons/{setup['season'].pk}/")).content.decode()
    assert "/games/new/" not in page
    assert f"/games/{setup['game'].pk}/edit/" not in page


def test_season_with_no_games_says_so_with_add_button_for_commissioner(client, setup):
    setup["game"].delete()
    client.force_login(setup["jordan"])
    page = client.get(url(setup, f"seasons/{setup['season'].pk}/")).content.decode()
    assert "No games yet" in page
    assert "/games/new/" not in page
    client.force_login(setup["commissioner"])
    page = client.get(url(setup, f"seasons/{setup['season'].pk}/")).content.decode()
    assert "No games yet" in page
    assert f"/seasons/{setup['season'].pk}/games/new/" in page


def test_game_detail_lists_each_players_status_with_edit_links_per_policy(client, setup):
    game = setup["game"]
    players = setup["players"]
    StatLine.objects.create(player=players["Jordan"], game=game, values={"receptions": 5})
    StatLine.objects.create(player=players["Corey"], game=game, did_not_play=True)

    def entry(name):
        return f"/games/{game.pk}/stats/{players[name].pk}/"

    client.force_login(setup["jordan"])
    page = client.get(url(setup, f"games/{game.pk}/")).content.decode()
    for text in ("Jordan", "Corey", "Casey", "Entered", "DNP", "Missing"):
        assert text in page
    assert entry("Jordan") in page
    assert entry("Corey") not in page
    assert entry("Casey") not in page

    client.force_login(setup["commissioner"])
    page = client.get(url(setup, f"games/{game.pk}/")).content.decode()
    for name in ("Jordan", "Corey", "Casey"):
        assert entry(name) in page


# --- Adding a game with player stats in one step ---------------------------------

def new_game_url(setup):
    return url(setup, f"seasons/{setup['season'].pk}/games/new/")


def stats_for(player, **values):
    """POST data for one player's section of the add-game form."""
    return {f"player-{player.pk}-{key}": value for key, value in values.items()}


def test_add_game_page_has_a_stat_section_for_each_active_player_on_the_team(client, setup):
    from leagues.models import Player, Team

    players = setup["players"]
    retired = Player.objects.create(
        league=setup["league"], team=setup["team"], name="Retiree", primary_position="WR", status="Retired"
    )
    other_team = Team.objects.create(league=setup["league"], name="Other Team")
    elsewhere = Player.objects.create(league=setup["league"], team=other_team, name="Elsewhere", primary_position="WR")
    client.force_login(setup["commissioner"])
    page = client.get(new_game_url(setup)).content.decode()
    for name in ("Jordan", "Corey", "Casey"):
        assert f'name="player-{players[name].pk}-did_not_play"' in page
    assert f"player-{retired.pk}-" not in page
    assert f"player-{elsewhere.pk}-" not in page


def test_adding_a_game_saves_entered_stat_lines_and_skips_blank_players(client, setup):
    players = setup["players"]
    data = dict(GAME)
    data.update(stats_for(players["Jordan"], receptions="5", receiving_yards="-3"))
    data.update(stats_for(players["Corey"], did_not_play="on", receptions="9"))
    client.force_login(setup["commissioner"])
    response = client.post(new_game_url(setup), data)
    assert response.status_code == 302
    game = Game.objects.get(opponent="Owls")
    jordan_line = StatLine.objects.get(game=game, player=players["Jordan"])
    assert (jordan_line.did_not_play, jordan_line.values) == (False, {"receptions": 5, "receiving_yards": -3})
    corey_line = StatLine.objects.get(game=game, player=players["Corey"])
    assert (corey_line.did_not_play, corey_line.values) == (True, {})
    assert not StatLine.objects.filter(game=game, player=players["Casey"]).exists()


def test_adding_a_game_with_no_stats_still_works(client, setup):
    client.force_login(setup["commissioner"])
    response = client.post(new_game_url(setup), GAME)
    assert response.status_code == 302
    game = Game.objects.get(opponent="Owls")
    assert not game.stat_lines.exists()


def test_an_invalid_stat_saves_neither_the_game_nor_any_line(client, setup):
    players = setup["players"]
    data = dict(GAME)
    data.update(stats_for(players["Jordan"], receptions="5"))
    data.update(stats_for(players["Corey"], receptions="1e30"))
    client.force_login(setup["commissioner"])
    response = client.post(new_game_url(setup), data)
    assert response.status_code == 200
    assert "too large" in response.content.decode()
    assert not Game.objects.filter(opponent="Owls").exists()
    assert StatLine.objects.filter(player__in=players.values()).count() == 0


def test_an_invalid_game_keeps_the_entered_stats_on_the_page(client, setup):
    players = setup["players"]
    data = dict(GAME, opponent="")
    data.update(stats_for(players["Jordan"], receptions="7"))
    client.force_login(setup["commissioner"])
    response = client.post(new_game_url(setup), data)
    assert response.status_code == 200
    page = response.content.decode()
    assert f'name="player-{players["Jordan"].pk}-receptions"' in page and 'value="7"' in page
    assert not Game.objects.filter(order=2).exists()
