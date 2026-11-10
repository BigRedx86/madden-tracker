"""Parsing and normalizing the old app's backup (U8: R23, R26). Pure functions, no database."""

import json
from pathlib import Path

import pytest

from importer import legacy

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "legacy_sample.json"


@pytest.fixture
def raw():
    return json.loads(FIXTURE.read_text())


def player_named(parsed, name):
    return next(p for p in parsed["players"] if p["name"] == name)


def test_bio_fields_are_normalized(raw):
    avery = player_named(legacy.parse(raw), "Avery")
    assert avery["height"] == "6'2\""
    assert avery["weight"] == 170
    assert avery["overall"] is None
    assert (avery["primary_position"], avery["secondary_position"]) == ("WR", "CB")
    assert avery["team"] == "Sharks"
    assert player_named(legacy.parse(raw), "Ellis")["overall"] == 71


def test_old_rb_position_becomes_hb(raw):
    assert player_named(legacy.parse(raw), "Blake")["primary_position"] == "HB"


def test_seasons_and_games_keep_their_order(raw):
    casey = player_named(legacy.parse(raw), "Casey")
    assert [s["name"] for s in casey["seasons"]] == ["01-02", "02-03 Season"]
    assert [g["opponent"] for g in casey["seasons"][0]["games"]][:4] == ["49ers", "Chiefs", "Vikings", "Raiders"]


def test_old_stat_keys_map_to_catalog_keys_and_total_tackles_is_dropped(raw):
    first = raw["players"][0]["seasons"][0]["games"][0]
    values = player_named(legacy.parse(raw), "Avery")["seasons"][0]["games"][0]["values"]
    assert values["receiving_yards"] == first["offense"]["receivingYards"]
    assert values["passes_defended"] == first["defense"]["passDeflections"]
    assert values["forced_fumbles"] == first["defense"]["forceFumbles"]
    assert "total_tackles" not in values


def test_dnp_with_all_zero_stats_becomes_an_empty_dnp_line(raw):
    bengals = player_named(legacy.parse(raw), "Drew")["seasons"][0]["games"][4]
    assert bengals["did_not_play"] is True
    assert bengals["values"] == {}


def test_negative_rushing_yards_and_half_sacks_survive(raw):
    parsed = legacy.parse(raw)
    assert player_named(parsed, "Blake")["seasons"][0]["games"][0]["values"]["rushing_yards"] == -3
    drew = player_named(parsed, "Drew")["seasons"][0]["games"]
    assert (drew[0]["values"]["sacks"], drew[1]["values"]["sacks"]) == (0.5, 1.5)


@pytest.mark.parametrize(
    "data",
    [
        {"teams": []},
        [],
        {"players": "nope"},
        {"players": [{"name": "X"}]},
        {"players": [{"name": "X", "primaryPosition": "WR", "seasons": [{"name": "1", "games": [{"opponent": 3}]}]}]},
    ],
)
def test_wrong_shape_raises_a_readable_error(data):
    with pytest.raises(legacy.LegacyFormatError) as error:
        legacy.parse(data)
    assert str(error.value)


@pytest.mark.parametrize(
    "raw_score, outcome, expected",
    [
        ("38-28", "L", ("ok", 28, 38)),
        ("24-14", "W", ("ok", 24, 14)),
        ("9-16", "L", ("ok", 9, 16)),
        ("16-9", "L", ("ok", 9, 16)),
        ("35-10", "L", ("ok", 10, 35)),
        (" 21 - 21 ", "T", ("ok", 21, 21)),
        ("", "W", ("missing", None, None)),
        ("38=28", "L", ("malformed", None, None)),
        ("338-7", "L", ("malformed", None, None)),
        ("24-14", "", ("malformed", None, None)),
        ("24-14", "T", ("malformed", None, None)),
    ],
)
def test_parse_score(raw_score, outcome, expected):
    assert legacy.parse_score(raw_score, outcome) == expected


def _first_game(data):
    return data["players"][0]["seasons"][0]["games"][0]


def _set_unknown_stat(data):
    _first_game(data)["offense"]["passingYardz"] = 1


def _set_dnp_with_yards(data):
    game = _first_game(data)
    game["isDNP"] = True
    game["offense"]["receivingYards"] = 26


def _set_status_gone(data):
    data["players"][0]["status"] = "Gone"


def _set_position_xx(data):
    data["players"][0]["primaryPosition"] = "XX"


def _set_garbled_weight(data):
    data["players"][0]["weight"] = "12x"


def _set_superscript_weight(data):
    data["players"][0]["weight"] = "²"


def _set_garbled_stat(data):
    _first_game(data)["offense"]["receptions"] = "12x"


def _set_player_not_object(data):
    data["players"][0] = "nope"


def _set_game_not_object(data):
    data["players"][0]["seasons"][0]["games"][0] = ["nope"]


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (_set_unknown_stat, "unknown stat 'passingYardz'"),
        (_set_dnp_with_yards, "is marked did-not-play but has stats"),
        (_set_status_gone, "status should be Active or Retired"),
        (_set_position_xx, "unknown primary position 'XX'"),
        (_set_garbled_weight, "weight should be a whole number, not '12x'"),
        (_set_superscript_weight, "weight should be a whole number, not '²'"),
        (_set_garbled_stat, "Player 'Avery', season 1 (01-02), game 1: Receptions must be a number."),
        (_set_player_not_object, "Player 1 should be an object"),
        (_set_game_not_object, "game 1 should be an object"),
    ],
    ids=lambda value: value.__name__.removeprefix("_set_") if callable(value) else None,
)
def test_bad_values_raise_a_legacy_format_error(raw, mutate, fragment):
    assert legacy.parse(raw)  # the unmutated fixture is valid
    mutate(raw)
    with pytest.raises(legacy.LegacyFormatError) as error:
        legacy.parse(raw)
    assert fragment in str(error.value)
