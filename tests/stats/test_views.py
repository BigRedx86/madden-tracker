"""Player profile, comparison, and team season pages (U7: R18, R20-R22, R9)."""

import re

import pytest

from leagues.models import Game, Season
from stats.models import StatLine


@pytest.fixture
def setup(make_user, make_league):
    commissioner = make_user("comm")
    noel = make_user("noel")
    built = make_league("Lakeside", commissioner, members=[(noel, "Noel")], players=("Noel", "Quinn", "Corey"))
    built.update(commissioner=commissioner, noel=noel)
    players = built["players"]
    players["Quinn"].primary_position = "QB"
    players["Quinn"].save()
    players["Corey"].primary_position = "CB"
    players["Corey"].save()
    return built


def url(setup, path):
    return f"/league/{setup['league'].pk}/{path}"


def add_games(season, count, start=1, **scores):
    scores = scores or {"team_score": 21, "opponent_score": 14}
    return [
        Game.objects.create(season=season, order=start + i, opponent=f"Opp{start + i}", **scores) for i in range(count)
    ]


def cells(page, label):
    """The text cells of the table row whose first cell is ``label`` (per-game averages left out)."""
    page = re.sub(r'<span class="per-game">.*?</span>', "", page)
    match = re.search(r"<tr>\s*<t[hd][^>]*>\s*" + re.escape(label) + r"\s*</t[hd]>(.*?)</tr>", page, re.S)
    assert match, f"no row {label!r}"
    return [re.sub(r"<[^>]+>", "", cell).strip() for cell in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", match.group(1), re.S)]


# --- Profile (R20) ---------------------------------------------------------------------


def test_profile_shows_bio_totals_per_season_and_game_log(client, setup):
    noel = setup["players"]["Noel"]
    noel.height, noel.weight, noel.awards = "6'1\"", 195, ["OPOY"]
    noel.save()
    season_one = setup["season"]
    season_two = Season.objects.create(team=setup["team"], name="02-03")
    game_two = add_games(season_two, 1)[0]
    StatLine.objects.create(player=noel, game=setup["game"], values={"receptions": 4, "receiving_yards": 60})
    StatLine.objects.create(player=noel, game=game_two, values={"receptions": 2, "receiving_yards": 15})

    client.force_login(setup["noel"])
    page = client.get(url(setup, f"players/{noel.pk}/")).content.decode()

    assert "6&#x27;1&quot;" in page and "195 lbs" in page and "OPOY" in page
    receiving = page.split('id="season-receiving"')[1].split("</table>")[0]
    # Season label, GP, Receptions, Receiving yards, ...
    assert cells(receiving, "Lakeside Team 01-02")[:3] == ["1", "4", "60"]
    assert cells(receiving, "Lakeside Team 02-03")[:3] == ["1", "2", "15"]
    assert cells(receiving, "Career")[:3] == ["2", "6", "75"]
    assert cells(receiving, "Career per game")[:3] == ["", "3.0", "37.5"]
    # Game log, with each game's result.
    assert "Week 1 vs Sharks" in page and "Week 1 vs Opp1" in page
    assert 'result--w' in page


def test_profile_averages_exclude_dnp(client, setup):
    noel = setup["players"]["Noel"]
    games = [setup["game"], *add_games(setup["season"], 11, start=2)]
    for game in games[:11]:
        StatLine.objects.create(player=noel, game=game, values={"receiving_yards": 5})
    StatLine.objects.create(player=noel, game=games[11], did_not_play=True)
    client.force_login(setup["noel"])
    page = client.get(url(setup, f"players/{noel.pk}/")).content.decode()
    receiving = page.split('id="season-receiving"')[1].split("</table>")[0]
    row = cells(receiving, "Career per game")
    assert "5.0" in row and "4.6" not in row
    assert "DNP" in page


def test_correcting_a_stat_line_changes_profile_totals_on_next_request(client, setup):
    noel = setup["players"]["Noel"]
    stat_line = StatLine.objects.create(player=noel, game=setup["game"], values={"receptions": 4})
    client.force_login(setup["noel"])
    profile = url(setup, f"players/{noel.pk}/")
    receiving = client.get(profile).content.decode().split('id="season-receiving"')[1]
    assert cells(receiving, "Career")[:2] == ["1", "4"]

    stat_line.values = {"receptions": 7}
    stat_line.save()
    receiving = client.get(profile).content.decode().split('id="season-receiving"')[1]
    assert cells(receiving, "Career")[:2] == ["1", "7"]


def test_profile_with_no_lines_says_no_games_logged(client, setup):
    client.force_login(setup["noel"])
    page = client.get(url(setup, f"players/{setup['players']['Quinn'].pk}/")).content.decode()
    assert "No games logged yet" in page


def test_profile_of_another_leagues_player_is_404(client, setup, make_user, make_league):
    # Covers AE7.
    other = make_league("Faraway", make_user("other-comm"), players=("Remote",))
    client.force_login(setup["noel"])
    assert client.get(url(setup, f"players/{other['players']['Remote'].pk}/")).status_code == 404
    assert client.get(f"/league/{other['league'].pk}/players/{other['players']['Remote'].pk}/").status_code == 404


def test_roster_links_player_names_to_profiles(client, setup):
    client.force_login(setup["noel"])
    page = client.get(url(setup, "roster/")).content.decode()
    assert f'href="/league/{setup["league"].pk}/players/{setup["players"]["Quinn"].pk}/"' in page


# --- Comparison (R21) ------------------------------------------------------------------


def compare_url(setup, players, scope="career"):
    query = "&".join(f"players={p.pk}" for p in players)
    return url(setup, f"compare/?{query}&scope={scope}")


def test_comparing_a_qb_with_a_cb_shows_both_categories_with_dashes(client, setup):
    quinn, corey = setup["players"]["Quinn"], setup["players"]["Corey"]
    StatLine.objects.create(player=quinn, game=setup["game"], values={"passing_yards": 250, "passing_tds": 2})
    StatLine.objects.create(player=corey, game=setup["game"], values={"solo_tackles": 5, "assist_tackles": 1})
    client.force_login(setup["noel"])
    page = client.get(compare_url(setup, [quinn, corey])).content.decode()
    table = page.split('class="compare-table')[1].split("</table>")[0]
    assert cells(table, "Passing yards") == ["250", "—"]
    assert cells(table, "Total tackles") == ["—", "6"]
    assert "Receiving yards" not in table
    # Phone layout: one card per player.
    assert page.count('class="card compare-card"') == 2


def test_compare_one_season_only_counts_that_season(client, setup):
    quinn, corey = setup["players"]["Quinn"], setup["players"]["Corey"]
    later = Season.objects.create(team=setup["team"], name="02-03")
    later_game = add_games(later, 1)[0]
    StatLine.objects.create(player=quinn, game=setup["game"], values={"passing_yards": 100})
    StatLine.objects.create(player=quinn, game=later_game, values={"passing_yards": 300})
    client.force_login(setup["noel"])
    season_page = client.get(compare_url(setup, [quinn, corey], scope=later.pk)).content.decode()
    career_page = client.get(compare_url(setup, [quinn, corey])).content.decode()
    assert cells(season_page.split('class="compare-table')[1], "Passing yards") == ["300", "—"]
    assert cells(career_page.split('class="compare-table')[1], "Passing yards") == ["400", "—"]


def test_compare_with_a_foreign_player_is_404(client, setup, make_user, make_league):
    other = make_league("Faraway", make_user("other-comm"), players=("Remote",))
    client.force_login(setup["noel"])
    response = client.get(compare_url(setup, [setup["players"]["Quinn"], other["players"]["Remote"]]))
    assert response.status_code == 404


def test_compare_needs_two_players(client, setup):
    client.force_login(setup["noel"])
    page = client.get(compare_url(setup, [setup["players"]["Quinn"]])).content.decode()
    assert "Pick at least two players" in page
    assert "compare-table" not in page
    blank = client.get(url(setup, "compare/")).content.decode()
    assert 'name="players"' in blank and "Pick at least two players" not in blank


# --- Team season (R22) -----------------------------------------------------------------


def test_team_season_shows_record_with_playoffs_separate(client, setup):
    season = setup["season"]  # Already has one regular-season win.
    add_games(season, 9, start=2)
    add_games(season, 7, start=11, team_score=7, opponent_score=21)
    Game.objects.create(season=season, order=18, opponent="Wolves", team_score=30, opponent_score=3, is_playoff=True)
    client.force_login(setup["noel"])
    page = client.get(url(setup, f"seasons/{season.pk}/stats/")).content.decode()
    assert "Record: 10-7" in page
    assert "Playoffs: 1-0" in page
    assert "Week 18 vs Wolves" in page and "Playoff" in page


def test_team_season_leaders_and_ties(client, setup):
    noel, quinn = setup["players"]["Noel"], setup["players"]["Quinn"]
    StatLine.objects.create(player=noel, game=setup["game"], values={"rushing_yards": 40})
    StatLine.objects.create(player=quinn, game=setup["game"], values={"rushing_yards": 40, "passing_yards": 210})
    client.force_login(setup["noel"])
    page = client.get(url(setup, f"seasons/{setup['season'].pk}/stats/")).content.decode()
    leaders = page.split('id="leaders"')[1]
    assert cells(leaders, "Rushing yards") == ["Noel, Quinn", "40"]
    assert cells(leaders, "Passing yards") == ["Quinn", "210"]


def test_team_season_of_another_league_is_404(client, setup, make_user, make_league):
    other = make_league("Faraway", make_user("other-comm"))
    client.force_login(setup["noel"])
    assert client.get(url(setup, f"seasons/{other['season'].pk}/stats/")).status_code == 404


def test_nav_links_to_compare(client, setup):
    client.force_login(setup["noel"])
    page = client.get(url(setup, "")).content.decode()
    assert f'href="/league/{setup["league"].pk}/compare/"' in page

