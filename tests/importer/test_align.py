"""Matching each player's games into one schedule per season, and the conflicts (U8: R24, R25, AE4, AE5, KTD7).

Pure functions, no database.
"""

import json
from pathlib import Path

import pytest

from importer import align, legacy

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "legacy_sample.json"


def game(opponent, score="7-3", outcome="W", **values):
    return {"opponent": opponent, "score": score, "outcome": outcome, "is_playoff": False,
            "did_not_play": False, "values": values}


def parsed(*players):
    """players: (name, [games]) for one season named "S1"."""
    return {
        "players": [
            {"name": name, "team": "Sharks", "primary_position": "WR", "secondary_position": "", "height": "",
             "weight": None, "overall": None, "super_bowl_wins": 0, "awards": [], "status": "Active",
             "retirement_year": None, "seasons": [{"name": "S1", "games": games}]}
            for name, games in players
        ]
    }


@pytest.fixture
def sample():
    return align.propose(legacy.parse(json.loads(FIXTURE.read_text())))


def of_type(proposal, kind, season=None):
    return [c for c in proposal["conflicts"] if c["type"] == kind and (season is None or c["season"] == season)]


def accept_suggestions(proposal):
    return {c["id"]: {"choice": c["suggested"]} for c in proposal["conflicts"] if c["suggested"] is not None}


def all_resolved(proposal):
    """Every suggestion accepted, and unplaced games discarded."""
    return accept_suggestions(proposal) | {
        c["id"]: {"choice": "discard"} for c in proposal["conflicts"] if c["type"] == "unplaced"}


def lines_by_opponent(plan, season, player):
    return {
        (g["order"], g["opponent"]): line
        for g in plan["seasons"][season]["games"]
        for line in g["lines"]
        if line["player"] == player
    }


# --- AE5: rotated first games align by opponent ------------------------------------


def test_rotated_first_games_align_into_one_sequence_by_opponent():
    ref = [game("Raiders", rushing_yards=1), game("49ers", rushing_yards=2), game("Chiefs", rushing_yards=3),
           game("Vikings", rushing_yards=4)]
    rotated = [game("49ers", rushing_yards=20), game("Chiefs", rushing_yards=30), game("Vikings", rushing_yards=40),
               game("Raiders", rushing_yards=10)]
    proposal = align.propose(parsed(("A", ref), ("B", list(ref)), ("C", rotated)))
    season = proposal["seasons"][0]
    assert [slot["opponent"] for slot in season["slots"]] == ["Raiders", "49ers", "Chiefs", "Vikings"]

    moved = of_type(proposal, "moved")
    assert len(moved) == 1 and moved[0]["suggested"] == "slot:0" and moved[0]["player"] == 2

    resolutions = {c["id"]: {"choice": c["suggested"]} for c in proposal["conflicts"]}
    plan = align.build(proposal, resolutions)
    games = plan["seasons"][0]["games"]
    assert len(games) == 4
    c_yards = {g["opponent"]: line["values"]["rushing_yards"] for g in games for line in g["lines"] if line["player"] == 2}
    assert c_yards == {"Raiders": 10, "49ers": 20, "Chiefs": 30, "Vikings": 40}


def test_fixture_rotated_player_is_placed_with_a_moved_game_conflict(sample):
    moved = of_type(sample, "moved", season=0)
    assert [(c["player"], c["suggested"]) for c in moved] == [(2, "slot:0")]
    assert [slot["opponent"] for slot in sample["seasons"][0]["slots"]][:4] == ["Raiders", "49ers", "Chiefs", "Vikings"]


# --- AE4 and score conflicts --------------------------------------------------------


def test_338_7_is_flagged_with_38_7_suggested_and_not_accepted_until_chosen(sample):
    bills = next(c for c in of_type(sample, "score", season=0) if c["slot"] == 6)
    suggested = next(choice for choice in bills["choices"] if choice["value"] == bills["suggested"])
    assert (suggested["team_score"], suggested["opponent_score"]) == (7, 38)
    assert any("338-7" in detail for detail in bills["details"])
    with pytest.raises(align.NotReady):
        align.build(sample, {})


def test_38_equals_28_is_flagged_as_malformed(sample):
    bengals = next(c for c in of_type(sample, "score", season=0) if c["slot"] == 4)
    assert any("38=28" in detail for detail in bengals["details"])
    suggested = next(choice for choice in bengals["choices"] if choice["value"] == bengals["suggested"])
    assert (suggested["team_score"], suggested["opponent_score"]) == (28, 38)


def test_reversed_score_with_the_same_outcome_is_not_a_conflict(sample):
    flagged_slots = {c["slot"] for c in sample["conflicts"] if c["season"] == 0}
    assert 5 not in flagged_slots  # Patriots: "9-16" L and "16-9" L
    plan = align.build(sample, all_resolved(sample))
    patriots = plan["seasons"][0]["games"][5]
    assert (patriots["opponent"], patriots["team_score"], patriots["opponent_score"]) == ("Patriots", 9, 16)


def test_two_valid_but_different_scores_are_flagged_with_both(sample):
    texans = next(c for c in of_type(sample, "score", season=1) if c["slot"] == 6)
    labels = {(c["team_score"], c["opponent_score"]) for c in texans["choices"] if "team_score" in c}
    assert labels == {(27, 24), (27, 21)}
    assert texans["custom"] == "score"


# --- Missing scores -------------------------------------------------------------------


def test_missing_score_is_preselected_as_result_only_and_needs_acknowledging(sample):
    missing = of_type(sample, "missing_score", season=0)
    assert [c["slot"] for c in missing] == [3, 7, 8]
    patriots = next(c for c in missing if c["slot"] == 7)
    assert patriots["suggested"] == "result:W"
    resolutions = {c["id"]: {"choice": c["suggested"]} for c in sample["conflicts"] if c["type"] != "missing_score"}
    with pytest.raises(align.NotReady):
        align.build(sample, resolutions)


def test_season_wide_acknowledge_clears_only_that_seasons_missing_scores(sample):
    acknowledged = align.acknowledge_missing(sample, {}, season=1)
    missing_b = {c["id"] for c in of_type(sample, "missing_score", season=1)}
    assert len(missing_b) == 5
    assert set(acknowledged) == missing_b
    assert all(r["choice"].startswith("result:") for r in acknowledged.values())

    plan = align.build(sample, all_resolved(sample) | acknowledged)
    colts = plan["seasons"][1]["games"][0]
    assert (colts["team_score"], colts["opponent_score"], colts["recorded_result"]) == (None, None, "L")


def test_one_players_blank_score_does_not_hide_the_others_score():
    proposal = align.propose(parsed(("A", [game("Owls", "21-7", "W")]), ("B", [game("Owls", "", "W")])))
    assert proposal["conflicts"] == []
    played = align.build(proposal, {})["seasons"][0]["games"][0]
    assert (played["team_score"], played["opponent_score"]) == (21, 7)


# --- Repeated opponents and out-of-order games ---------------------------------------


def test_repeated_opponents_align_in_order_without_crossing(sample):
    plan = align.build(sample, all_resolved(sample))
    games = plan["seasons"][0]["games"]
    assert [g["opponent"] for g in games[5:9]] == ["Patriots", "Bills", "Patriots", "Bills"]
    assert (games[5]["result"], games[7]["result"]) == ("L", "W")
    assert (games[6]["result"], games[8]["result"]) == ("L", "L")


def test_bills_game_logged_last_is_placed_at_its_reference_slot(sample):
    moved = next(c for c in of_type(sample, "moved", season=1) if c["player"] == 1)
    assert moved["suggested"] == "slot:2"
    plan = align.build(sample, all_resolved(sample))
    blake = lines_by_opponent(plan, 1, 1)
    assert set(blake) == {(1, "Colts"), (2, "Patriots"), (3, "Bills"), (4, "Commanders"), (5, "Jags"),
                          (6, "Bills"), (7, "Texans")}


def test_jags_game_logged_last_is_placed_the_same_way(sample):
    moved = next(c for c in of_type(sample, "moved", season=1) if c["player"] == 2)
    assert moved["suggested"] == "slot:4"


def test_moved_game_can_be_put_in_another_slot():
    ref = [game("Owls", "1-0", "W"), game("Hawks", "1-0", "W"), game("Owls", "0-1", "L")]
    late = [game("Hawks", "1-0", "W"), game("Owls", "0-1", "L"), game("Owls", "1-0", "W", receptions=5)]
    proposal = align.propose(parsed(("A", ref), ("B", list(ref)), ("C", late)))
    moved = of_type(proposal, "moved")
    assert [c["suggested"] for c in moved] == ["slot:0"]  # outcome picks between the two Owls slots
    assert [c["value"] for c in moved[0]["choices"]] == ["slot:0"]
    plan = align.build(proposal, {moved[0]["id"]: {"choice": "slot:0"}})
    assert plan["seasons"][0]["games"][0]["lines"][-1] == {"player": 2, "did_not_play": False, "values": {"receptions": 5}}


def test_slot_with_different_opponents_is_flagged_with_both():
    a = [game("Colts"), game("Commanders"), game("Eagles")]
    b = [game("Colts"), game("Bills"), game("Eagles")]
    proposal = align.propose(parsed(("A", a), ("A2", list(a)), ("B", b)))
    [conflict] = of_type(proposal, "opponent")
    assert conflict["slot"] == 1
    assert {c["opponent"] for c in conflict["choices"]} == {"Commanders", "Bills"}
    assert conflict["suggested"] == conflict["choices"][0]["value"]
    assert conflict["choices"][0]["opponent"] == "Commanders"

    resolution = align.resolve(conflict, {"choice": "custom", "opponent": "Giants"})
    plan = align.build(proposal, {conflict["id"]: resolution})
    assert plan["seasons"][0]["games"][1]["opponent"] == "Giants"
    assert len(plan["seasons"][0]["games"][1]["lines"]) == 3


def test_fixture_opponent_disagreement_is_flagged(sample):
    [conflict] = of_type(sample, "opponent", season=0)
    assert conflict["slot"] == 2
    assert {c["opponent"] for c in conflict["choices"]} == {"Chiefs", "Jets"}


# --- Unplaced games -------------------------------------------------------------------


@pytest.fixture
def unplaced(sample):
    [conflict] = of_type(sample, "unplaced")
    return conflict


def ready(sample):
    return accept_suggestions(sample) | align.acknowledge_missing(sample, {}, 0) | align.acknowledge_missing(sample, {}, 1)


def test_game_with_no_qualifying_slot_is_unplaced(sample, unplaced):
    assert (unplaced["season"], unplaced["player"], unplaced["suggested"]) == (0, 1, None)
    assert {"new", "discard"} <= {c["value"] for c in unplaced["choices"]}


def test_unplaced_game_can_be_added_as_a_new_game(sample, unplaced):
    plan = align.build(sample, ready(sample) | {unplaced["id"]: {"choice": "new"}})
    games = plan["seasons"][0]["games"]
    assert len(games) == 10
    assert (games[9]["order"], games[9]["opponent"], games[9]["team_score"], games[9]["opponent_score"]) == (
        10, "Seahawks", 20, 17)
    assert [line["player"] for line in games[9]["lines"]] == [1]


def test_unplaced_game_can_be_discarded(sample, unplaced):
    plan = align.build(sample, ready(sample) | {unplaced["id"]: {"choice": "discard"}})
    assert len(plan["seasons"][0]["games"]) == 9
    assert sum(1 for g in plan["seasons"][0]["games"] for line in g["lines"] if line["player"] == 1) == 9


def test_unplaced_game_offers_only_slots_the_player_has_no_line_in(sample, unplaced):
    assert not [c for c in unplaced["choices"] if c["value"].startswith("slot:")]
    with pytest.raises(align.NotReady):
        align.build(sample, ready(sample) | {unplaced["id"]: {"choice": "slot:0"}})


def test_unplaced_game_can_attach_to_a_free_slot():
    ref = [game("Owls"), game("Hawks")]
    short = [game("Owls"), game("Ravens", receptions=3), game("Bears")]
    proposal = align.propose(parsed(("A", ref), ("A2", list(ref)), ("B", short)))
    kinds = sorted(c["type"] for c in proposal["conflicts"])
    assert kinds == ["unplaced", "unplaced"]
    ravens = next(c for c in proposal["conflicts"] if "Ravens" in c["summary"])
    bears = next(c for c in proposal["conflicts"] if "Bears" in c["summary"])
    plan = align.build(proposal, {ravens["id"]: {"choice": "slot:1"}, bears["id"]: {"choice": "discard"}})
    hawks = plan["seasons"][0]["games"][1]
    assert hawks["opponent"] == "Hawks"
    assert hawks["lines"][-1] == {"player": 2, "did_not_play": False, "values": {"receptions": 3}}

    with pytest.raises(align.NotReady) as error:
        align.build(proposal, {ravens["id"]: {"choice": "slot:1"}, bears["id"]: {"choice": "slot:1"}})
    assert "already" in str(error.value)


# --- Resolution input -----------------------------------------------------------------


def test_resolve_accepts_only_listed_choices_or_valid_custom_values(sample):
    score = of_type(sample, "score")[0]
    with pytest.raises(ValueError):
        align.resolve(score, {"choice": "nonsense"})
    with pytest.raises(ValueError):
        align.resolve(score, {"choice": "custom", "team_score": "x", "opponent_score": "3"})
    assert align.resolve(score, {"choice": "custom", "team_score": "21", "opponent_score": "3"}) == {
        "choice": "custom", "team_score": 21, "opponent_score": 3}


def test_resolved_count(sample):
    total = len(sample["conflicts"])
    assert align.resolved_count(sample, {}) == 0
    assert align.resolved_count(sample, ready(sample)) == total - 1  # the unplaced game has no suggestion


def test_whole_fixture_builds_with_two_seasons_of_9_and_7_games(sample, unplaced):
    plan = align.build(sample, ready(sample) | {unplaced["id"]: {"choice": "discard"}})
    assert [len(s["games"]) for s in plan["seasons"]] == [9, 7]
    assert [s["name"] for s in plan["seasons"]] == ["01-02", "02-03 Season"]
    assert {s["team"] for s in plan["seasons"]} == {"Sharks"}
    for season in plan["seasons"]:
        for g in season["games"]:
            assert sorted(line["player"] for line in g["lines"]) == [0, 1, 2, 3, 4]
