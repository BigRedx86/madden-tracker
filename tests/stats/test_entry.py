"""The stat entry form (U6: R15-R17, AE1, AE6, KTD6)."""

import re

import pytest

from leagues.models import Game, Player
from stats.models import StatLine


@pytest.fixture
def setup(make_user, make_league):
    commissioner = make_user("comm")
    alex = make_user("alex")
    blake = make_user("blake")
    built = make_league(
        "Lakeside", commissioner, members=[(alex, "Alex"), (blake, "Blake")], players=("Alex", "Blake")
    )
    built.update(commissioner=commissioner, alex=alex, blake=blake)
    return built


def entry_url(setup, player, game=None):
    game = game or setup["game"]
    return f"/league/{setup['league'].pk}/games/{game.pk}/stats/{player.pk}/"


def set_positions(player, primary, secondary=""):
    player.primary_position, player.secondary_position = primary, secondary
    player.save()


def main_categories(page):
    """Category headings shown before the "show all" section."""
    main = page.split('class="extra-categories"')[0]
    return re.findall(r'<legend>([A-Za-z]+)</legend>', main)


def test_qb_form_shows_passing_rushing_fumbles_only(client, setup):
    # Covers AE6.
    alex = setup["players"]["Alex"]
    set_positions(alex, "QB")
    client.force_login(setup["alex"])
    page = client.get(entry_url(setup, alex)).content.decode()
    assert main_categories(page) == ["Passing", "Rushing", "Fumbles"]


def test_wr_cb_form_shows_receiving_rushing_fumbles_and_defense(client, setup):
    alex = setup["players"]["Alex"]
    set_positions(alex, "WR", "CB")
    client.force_login(setup["alex"])
    shown = main_categories(client.get(entry_url(setup, alex)).content.decode())
    for category in ("Receiving", "Rushing", "Fumbles", "Defense"):
        assert category in shown
    assert "Kicking" not in shown and "Punting" not in shown


def test_show_all_lets_an_lb_save_a_rushing_td(client, setup):
    alex = setup["players"]["Alex"]
    set_positions(alex, "LB")
    client.force_login(setup["alex"])
    page = client.get(entry_url(setup, alex) + "?all=1").content.decode()
    assert '<details class="extra-categories" open' in page
    assert 'name="rushing_tds"' in page
    client.post(entry_url(setup, alex), {"rushing_tds": "1"})
    assert StatLine.objects.get(player=alex).values == {"rushing_tds": 1}


def test_alex_saves_his_line_and_gets_403_on_blakes(client, setup):
    # Covers AE1.
    week3 = Game.objects.create(season=setup["season"], order=3, opponent="Owls", team_score=7, opponent_score=3)
    alex, blake = setup["players"]["Alex"], setup["players"]["Blake"]
    client.force_login(setup["alex"])
    response = client.post(entry_url(setup, alex, week3), {"receptions": "6", "receiving_yards": "88"})
    assert response.status_code == 302
    assert StatLine.objects.get(player=alex, game=week3).values == {"receptions": 6, "receiving_yards": 88}

    assert client.post(entry_url(setup, blake, week3), {"receptions": "1"}).status_code == 403
    assert not StatLine.objects.filter(player=blake).exists()


def test_commissioner_enters_an_unclaimed_players_line(client, setup):
    casey = Player.objects.create(league=setup["league"], name="Casey", primary_position="HB")
    client.force_login(setup["commissioner"])
    client.post(entry_url(setup, casey), {"rushing_yards": "40"})
    assert StatLine.objects.get(player=casey).values == {"rushing_yards": 40}


def test_dnp_saves_an_empty_line_and_the_form_disables_stat_inputs(client, setup):
    alex = setup["players"]["Alex"]
    client.force_login(setup["alex"])
    client.post(entry_url(setup, alex), {"did_not_play": "on", "receptions": "3"})
    line = StatLine.objects.get(player=alex)
    assert line.did_not_play is True
    assert line.values == {}
    page = client.get(entry_url(setup, alex)).content.decode()
    assert re.search(r'<fieldset class="stat-fields"[^>]*\bdisabled\b', page)


def test_text_in_a_numeric_field_shows_an_error_and_saves_nothing(client, setup):
    alex = setup["players"]["Alex"]
    client.force_login(setup["alex"])
    response = client.post(entry_url(setup, alex), {"receptions": "lots", "receiving_yards": "20"})
    assert response.status_code == 200
    assert "Receptions must be a number." in response.content.decode()
    assert not StatLine.objects.exists()


def test_negative_rushing_yards_and_half_sacks_save(client, setup):
    alex = setup["players"]["Alex"]
    set_positions(alex, "HB", "LB")
    client.force_login(setup["alex"])
    client.post(entry_url(setup, alex), {"rushing_yards": "-3", "sacks": "0.5"})
    assert StatLine.objects.get(player=alex).values == {"rushing_yards": -3, "sacks": 0.5}


def test_blank_inputs_are_absent_keys(client, setup):
    alex = setup["players"]["Alex"]
    client.force_login(setup["alex"])
    client.post(entry_url(setup, alex), {"receptions": "2", "receiving_yards": "", "drops": ""})
    assert StatLine.objects.get(player=alex).values == {"receptions": 2}


def test_inputs_follow_value_types(client, setup):
    alex = setup["players"]["Alex"]
    set_positions(alex, "HB", "LB")
    client.force_login(setup["alex"])
    page = client.get(entry_url(setup, alex)).content.decode()

    def tag(name):
        return re.search(rf'<input[^>]*name="{name}"[^>]*>', page).group(0)

    assert 'step="0.5"' in tag("sacks") and 'inputmode="decimal"' in tag("sacks")
    assert 'min="0"' in tag("receptions") and 'inputmode="numeric"' in tag("receptions")
    assert "min=" not in tag("rushing_yards")


def test_editing_shows_existing_values(client, setup):
    alex = setup["players"]["Alex"]
    StatLine.objects.create(player=alex, game=setup["game"], values={"receptions": 7})
    client.force_login(setup["alex"])
    page = client.get(entry_url(setup, alex)).content.decode()
    assert re.search(r'name="receptions"[^>]*value="7"|value="7"[^>]*name="receptions"', page)
    client.post(entry_url(setup, alex), {"receptions": "8"})
    assert StatLine.objects.get(player=alex).values == {"receptions": 8}


def test_save_returns_to_the_game_list_with_next_unentered_game_highlighted(client, setup):
    alex = setup["players"]["Alex"]
    week2 = Game.objects.create(season=setup["season"], order=2, opponent="Owls", team_score=7, opponent_score=3)
    client.force_login(setup["alex"])
    response = client.post(entry_url(setup, alex), {"receptions": "1"}, follow=True)
    assert response.redirect_chain[-1][0].startswith(f"/league/{setup['league'].pk}/seasons/{setup['season'].pk}/")
    page = response.content.decode()
    highlighted = page[page.index("game--next"):]
    highlighted = highlighted[: highlighted.index("</li>")]
    assert "Owls" in highlighted
    assert f"/games/{week2.pk}/stats/{alex.pk}/" in highlighted
