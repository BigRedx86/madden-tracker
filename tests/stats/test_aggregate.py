"""Totals, averages, derived stats, leaders, and records (U7: R18, R19, R22, AE3, KTD6).

Pure functions: stat lines here are unsaved StatLine objects, no database.
"""

from leagues.models import Game
from stats import aggregate
from stats.models import StatLine


def line(dnp=False, **values):
    return StatLine(did_not_play=dnp, values=values)


def test_averages_count_only_games_played_ae3():
    # Covers AE3: 11 played games (55 yards in all) plus 1 DNP -> 5.0 per game, not 4.58.
    lines = [line(receiving_yards=5) for _ in range(11)] + [line(dnp=True)]
    summary = aggregate.summarize(lines)
    assert (summary.games_played, summary.did_not_play) == (11, 1)
    assert summary.total("receiving_yards") == 55
    assert summary.average("receiving_yards") == 5.0
    assert aggregate.format_average(summary.average("receiving_yards")) == "5.0"


def test_totals_sum_across_lines_and_absent_keys_count_as_zero():
    summary = aggregate.summarize([line(receptions=3, receiving_yards=40), line(receptions=2), line(receptions=1)])
    assert summary.total("receptions") == 6
    assert summary.total("receiving_yards") == 40
    # A receiving stat nobody entered still totals 0: the category has data.
    assert summary.total("receiving_tds") == 0


def test_career_totals_equal_the_sum_of_season_totals():
    season_one = [line(rushing_yards=30), line(rushing_yards=-3)]
    season_two = [line(rushing_yards=12)]
    first, second = aggregate.summarize(season_one), aggregate.summarize(season_two)
    career = aggregate.summarize(season_one + season_two)
    assert (first.total("rushing_yards"), second.total("rushing_yards")) == (27, 12)
    assert career.total("rushing_yards") == first.total("rushing_yards") + second.total("rushing_yards") == 39


def test_longest_stats_use_max_not_sum_and_skip_absent_keys():
    summary = aggregate.summarize(
        [line(receptions=1, receiving_longest=22), line(receptions=1, receiving_longest=48), line(receptions=2)]
    )
    assert summary.total("receiving_longest") == 48
    # Max stats are not averaged per game.
    assert summary.average("receiving_longest") is None


def test_longest_with_no_values_is_a_dash_not_zero():
    summary = aggregate.summarize([line(receptions=2)])
    assert summary.total("receiving_longest") is None
    assert aggregate.format_total(summary.total("receiving_longest")) == aggregate.DASH


def test_total_tackles_is_solo_plus_assisted():
    summary = aggregate.summarize([line(solo_tackles=4, assist_tackles=2), line(solo_tackles=3)])
    assert summary.total("total_tackles") == 9


def test_half_sacks_sum_and_format():
    summary = aggregate.summarize([line(sacks=0.5), line(sacks=1.5), line(sacks=0.5)])
    assert aggregate.format_total(summary.total("sacks")) == "2.5"
    assert aggregate.format_total(aggregate.summarize([line(sacks=1.5), line(sacks=0.5)]).total("sacks")) == "2"


def test_zero_games_played_gives_a_dash_for_averages():
    summary = aggregate.summarize([line(dnp=True)])
    assert summary.games_played == 0
    assert summary.average("receiving_yards") is None
    assert aggregate.format_average(summary.average("receiving_yards")) == aggregate.DASH
    assert aggregate.summarize([]).average("receptions") is None


def test_categories_are_only_those_with_data_in_catalog_order():
    summary = aggregate.summarize([line(solo_tackles=1), line(passing_yards=10, fumbles=1)])
    assert summary.categories == ["passing", "fumbles", "defense"]
    # A category without data has no totals (shown as a dash), not zeros.
    assert summary.total("receptions") is None
    assert summary.total("total_tackles") == 1


def test_derived_ratios_are_computed_from_totals():
    summary = aggregate.summarize(
        [line(passing_completions=6, passing_attempts=10), line(passing_completions=4, passing_attempts=10)]
    )
    assert summary.total("completion_pct") == 50.0
    assert aggregate.format_derived(summary.total("completion_pct")) == "50.0"


def test_leaders_pick_the_top_player_per_stat_and_ties_show_all():
    summaries = [
        ("Alpha", aggregate.summarize([line(receiving_yards=80, receptions=5)])),
        ("Bravo", aggregate.summarize([line(receiving_yards=80, receptions=3)])),
        ("Casey", aggregate.summarize([line(solo_tackles=6)])),
    ]
    groups = {group["label"]: {row["label"]: row for row in group["rows"]} for group in aggregate.leaders(summaries)}
    assert groups["Receiving"]["Receiving yards"]["players"] == ["Alpha", "Bravo"]
    assert groups["Receiving"]["Receiving yards"]["value"] == "80"
    assert groups["Receiving"]["Receptions"]["players"] == ["Alpha"]
    assert groups["Defense"]["Total tackles"]["players"] == ["Casey"]
    # Stats nobody recorded have no leader.
    assert "Receiving TDs" not in groups["Receiving"]
    assert "Passing" not in groups


def _game(team, opponent, playoff=False):
    return Game(order=1, opponent="X", team_score=team, opponent_score=opponent, is_playoff=playoff)


def test_record_counts_playoffs_separately():
    games = [_game(21, 7) for _ in range(10)] + [_game(7, 21) for _ in range(7)] + [_game(30, 3, playoff=True)]
    record = aggregate.season_record(games)
    assert record == {"regular": "10-7", "playoff": "1-0"}


def test_record_shows_ties_and_no_playoffs():
    assert aggregate.season_record([_game(7, 7), _game(14, 7)]) == {"regular": "1-0-1", "playoff": None}
