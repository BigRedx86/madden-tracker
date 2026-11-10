"""Stat catalog: value types, position map, legacy keys, derived stats (KTD6, R17)."""

import pytest
from django.core.exceptions import ValidationError

from stats import catalog


def test_unknown_key_is_rejected():
    with pytest.raises(ValidationError):
        catalog.normalize_values({"warp_speed": 3})


@pytest.mark.parametrize("value", [-1, "-1"])
def test_negative_value_rejected_for_non_yardage_stat(value):
    with pytest.raises(ValidationError):
        catalog.normalize_values({"receptions": value})


@pytest.mark.parametrize("value", [-3, "-3"])
def test_negative_rushing_yards_accepted(value):
    assert catalog.normalize_values({"rushing_yards": value}) == {"rushing_yards": -3}


@pytest.mark.parametrize("value", [0.5, "0.5", 1.5, "2"])
def test_sacks_accept_half_steps(value):
    assert catalog.normalize_values({"sacks": value})["sacks"] == float(value)


@pytest.mark.parametrize("value", [0.3, "0.3", -0.5, "1.25"])
def test_sacks_reject_anything_but_non_negative_half_steps(value):
    with pytest.raises(ValidationError):
        catalog.normalize_values({"sacks": value})


@pytest.mark.parametrize("value", [2.5, "2.5", True, "abc", "nan"])
def test_integer_stats_reject_fractions_and_non_numbers(value):
    with pytest.raises(ValidationError):
        catalog.normalize_values({"receptions": value})


def test_integer_strings_are_stored_as_ints():
    assert catalog.normalize_values({"receptions": " 4 ", "receiving_yards": "52"}) == {
        "receptions": 4,
        "receiving_yards": 52,
    }


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_blank_input_becomes_absent_key_not_zero(blank):
    values = catalog.normalize_values({"receptions": blank, "rushing_yards": "12"})
    assert values == {"rushing_yards": 12}
    assert "receptions" not in values


def test_explicit_zero_is_kept():
    assert catalog.normalize_values({"receptions": "0"}) == {"receptions": 0}


def test_yardage_and_count_stats_have_the_right_minimums():
    assert catalog.STATS["receiving_yards"].minimum is None
    assert catalog.STATS["kick_return_longest"].minimum is None
    assert catalog.STATS["receptions"].minimum == 0
    assert catalog.STATS["sacks"].minimum == 0


def test_longest_stats_combine_by_max_and_others_by_sum():
    assert catalog.STATS["rushing_longest"].combine == catalog.MAX
    assert catalog.STATS["field_goal_longest"].combine == catalog.MAX
    assert catalog.STATS["rushing_yards"].combine == catalog.SUM


# --- Position map (AE6) ------------------------------------------------------


def test_qb_shows_passing_rushing_fumbles():
    # Covers AE6.
    assert catalog.categories_for_positions("QB", "") == ["passing", "rushing", "fumbles"]


def test_wr_cb_shows_union_of_both_positions():
    assert catalog.categories_for_positions("WR", "CB") == [
        "rushing",
        "receiving",
        "returns",
        "fumbles",
        "defense",
    ]


@pytest.mark.parametrize(
    "positions, expected",
    [
        (("HB", ""), ["rushing", "receiving", "returns", "fumbles"]),
        (("FB", ""), ["rushing", "receiving", "returns", "fumbles"]),
        (("TE", ""), ["rushing", "receiving", "fumbles"]),
        (("LT", ""), ["fumbles"]),
        (("C", ""), ["fumbles"]),
        (("MLB", ""), ["defense"]),
        (("LB", ""), ["defense"]),
        (("LE", ""), ["defense"]),
        (("FS", ""), ["returns", "defense"]),
        (("SS", ""), ["returns", "defense"]),
        (("K", ""), ["kicking"]),
        (("P", ""), ["punting"]),
        (("K", "P"), ["kicking", "punting"]),
    ],
)
def test_position_map(positions, expected):
    assert catalog.categories_for_positions(*positions) == expected


def test_unknown_position_shows_nothing():
    assert catalog.categories_for_positions("XYZ", "") == []


def test_show_all_returns_every_category():
    expected = ["passing", "rushing", "receiving", "returns", "fumbles", "defense", "kicking", "punting"]
    assert catalog.categories_for_positions("XYZ", "", show_all=True) == expected
    assert catalog.categories_for_positions("QB", "", show_all=True) == expected


def test_every_category_has_stats_and_every_stat_has_a_known_category():
    categories = [key for key, _label in catalog.CATEGORIES]
    for category in categories:
        assert catalog.stats_for_categories([category]), category
    for stat in catalog.STATS.values():
        assert stat.category in categories


def test_returns_category_covers_kick_and_punt_returns():
    keys = {stat.key for stat in catalog.stats_for_categories(["returns"])}
    assert keys == {
        "kick_returns",
        "kick_return_yards",
        "kick_return_tds",
        "kick_return_longest",
        "punt_returns",
        "punt_return_yards",
        "punt_return_tds",
        "punt_return_longest",
    }


def test_legacy_rb_position_maps_to_hb():
    assert catalog.LEGACY_POSITIONS["RB"] == "HB"


# --- Legacy keys --------------------------------------------------------------

LEGACY_EXPECTED = {
    "receptions": "receptions",
    "receivingYards": "receiving_yards",
    "receivingTDs": "receiving_tds",
    "rushingAttempts": "rushing_attempts",
    "rushingYards": "rushing_yards",
    "rushingTDs": "rushing_tds",
    "fumbles": "fumbles",
    "soloTackles": "solo_tackles",
    "assistTackles": "assist_tackles",
    "totalTackles": "total_tackles",
    "tacklesForLoss": "tackles_for_loss",
    "sacks": "sacks",
    "interceptions": "interceptions",
    "passDeflections": "passes_defended",
    "forceFumbles": "forced_fumbles",
    "defensiveTDs": "defensive_tds",
}


@pytest.mark.parametrize("legacy_key, new_key", LEGACY_EXPECTED.items())
def test_every_legacy_key_maps_to_a_catalog_or_derived_stat(legacy_key, new_key):
    assert catalog.LEGACY_KEYS[legacy_key] == new_key
    assert new_key in catalog.STATS or new_key in catalog.DERIVED_STATS


def test_total_tackles_is_derived_not_stored():
    assert "total_tackles" in catalog.DERIVED_STATS
    assert "total_tackles" not in catalog.STATS
    with pytest.raises(ValidationError):
        catalog.normalize_values({"total_tackles": 5})


# --- Derived stats --------------------------------------------------------------


def test_derived_stat_list():
    assert set(catalog.DERIVED_STATS) == {
        "total_tackles",
        "completion_pct",
        "passer_rating",
        "yards_per_carry",
        "yards_per_catch",
        "yards_per_return",
        "field_goal_pct",
        "punt_average",
    }


def test_derived_values():
    totals = {
        "solo_tackles": 7,
        "assist_tackles": 3,
        "passing_completions": 20,
        "passing_attempts": 30,
        "passing_yards": 250,
        "passing_tds": 2,
        "passing_interceptions": 1,
        "rushing_attempts": 8,
        "rushing_yards": 30,
        "receptions": 4,
        "receiving_yards": 50,
        "kick_returns": 2,
        "kick_return_yards": 50,
        "punt_returns": 2,
        "punt_return_yards": 10,
        "field_goals_made": 3,
        "field_goal_attempts": 4,
        "punts": 4,
        "punt_yards": 180,
    }
    derived = catalog.derived_values(totals)
    assert derived["total_tackles"] == 10
    assert derived["completion_pct"] == pytest.approx(66.667, abs=0.001)
    # NFL formula by hand: a=1.8333, b=1.3333, c=1.3333, d=1.5417; sum/6*100
    assert derived["passer_rating"] == pytest.approx(100.69, abs=0.01)
    assert derived["yards_per_carry"] == pytest.approx(3.75)
    assert derived["yards_per_catch"] == pytest.approx(12.5)
    assert derived["yards_per_return"] == pytest.approx(15.0)
    assert derived["field_goal_pct"] == pytest.approx(75.0)
    assert derived["punt_average"] == pytest.approx(45.0)


def test_derived_values_with_no_denominator_are_none():
    derived = catalog.derived_values({})
    assert derived["total_tackles"] == 0
    for key in ("completion_pct", "passer_rating", "yards_per_carry", "yards_per_catch",
                "yards_per_return", "field_goal_pct", "punt_average"):
        assert derived[key] is None, key


# --- Magnitude bound: no single-game stat exceeds 99,999 -------------------------


@pytest.mark.parametrize("raw", ["1e1000000", "-1e1000000", "1e5000", "1e30", "100000", "-100000", 1e30])
def test_too_large_value_rejected(raw):
    with pytest.raises(ValidationError, match="too large"):
        catalog.clean_value(catalog.STATS["passing_yards"], raw)


def test_huge_exponent_is_rejected_quickly():
    import time

    start = time.perf_counter()
    for key in ("passing_yards", "sacks", "receptions"):
        with pytest.raises(ValidationError):
            catalog.clean_value(catalog.STATS[key], "1e1000000")
    assert time.perf_counter() - start < 0.5


@pytest.mark.parametrize("key, raw, expected", [
    ("passing_yards", "99999", 99999),
    ("rushing_yards", "-3", -3),
    ("rushing_yards", "-99999", -99999),
    ("sacks", "0.5", 0.5),
    ("receptions", "0", 0),
])
def test_values_within_bound_accepted(key, raw, expected):
    assert catalog.clean_value(catalog.STATS[key], raw) == expected


def _form(**data):
    from stats.forms import StatLineForm

    return StatLineForm(data=data)


@pytest.mark.parametrize("raw", ["1e1000000", "1e5000", "1e30", "100000"])
def test_form_rejects_too_large_values(raw):
    import time

    start = time.perf_counter()
    form = _form(passing_yards=raw)
    assert not form.is_valid()
    assert "passing_yards" in form.errors
    assert time.perf_counter() - start < 0.5


def test_form_accepts_realistic_values():
    form = _form(passing_yards="99999", rushing_yards="-3", sacks="0.5")
    assert form.is_valid(), form.errors
    assert form.cleaned_data["values"] == {"passing_yards": 99999, "rushing_yards": -3, "sacks": 0.5}


def test_form_caps_input_length():
    # Numerically fine (it is 1), but longer than any real entry.
    form = _form(passing_yards="0" * 20 + "1")
    assert not form.is_valid()
    assert "passing_yards" in form.errors
