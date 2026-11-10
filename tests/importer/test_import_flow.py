"""Upload, review, and confirm a legacy import through the UI (U8: R23-R26, F2, KTD7)."""

import json
from collections import Counter, defaultdict
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError

from importer.models import ImportDraft
from leagues.models import Game, League, Membership, Player, Season, Team
from stats import catalog
from stats.models import StatLine

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "legacy_sample.json"


@pytest.fixture
def setup(make_user):
    commissioner = make_user("comm")
    member = make_user("member")
    league = League.objects.create(name="Lakeside")
    Membership.objects.create(league=league, user=commissioner, is_commissioner=True)
    Membership.objects.create(league=league, user=member)
    return {"league": league, "commissioner": commissioner, "member": member}


def url(setup, path=""):
    return f"/league/{setup['league'].pk}/import/{path}"


def upload_file(client, setup, content, name="backup.json"):
    return client.post(url(setup), {"backup": SimpleUploadedFile(name, content, content_type="application/json")})


def upload_fixture(client, setup):
    return upload_file(client, setup, FIXTURE.read_bytes())


def draft(setup):
    return ImportDraft.objects.get(league=setup["league"])


def resolve_all(client, setup, unplaced="new"):
    for conflict in draft(setup).proposal["conflicts"]:
        choice = conflict["suggested"] or unplaced
        response = client.post(url(setup, "resolve/"), {"conflict": conflict["id"], "choice": choice})
        assert response.status_code == 302


def league_data_counts(league):
    return (
        Team.objects.filter(league=league).count(),
        Player.objects.filter(league=league).count(),
        Season.objects.filter(team__league=league).count(),
        Game.objects.filter(season__team__league=league).count(),
        StatLine.objects.filter(player__league=league).count(),
    )


# --- Access ---------------------------------------------------------------------------


def test_member_who_is_not_commissioner_gets_403(client, setup):
    client.force_login(setup["commissioner"])
    upload_fixture(client, setup)
    client.force_login(setup["member"])
    assert client.get(url(setup)).status_code == 403
    assert upload_fixture(client, setup).status_code == 403
    assert client.get(url(setup, "review/")).status_code == 403
    assert client.post(url(setup, "confirm/")).status_code == 403
    conflict = draft(setup).proposal["conflicts"][0]
    assert client.post(url(setup, "resolve/"), {"conflict": conflict["id"], "choice": "0"}).status_code == 403
    assert draft(setup).resolutions == {}


# --- Upload ---------------------------------------------------------------------------


def test_wrong_shape_json_is_rejected_with_a_message_and_no_draft(client, setup):
    client.force_login(setup["commissioner"])
    response = upload_file(client, setup, json.dumps({"teams": []}).encode())
    assert response.status_code == 200
    assert "no list of players" in response.content.decode()
    assert not ImportDraft.objects.exists()


def test_invalid_json_is_rejected(client, setup):
    client.force_login(setup["commissioner"])
    response = upload_file(client, setup, b"{not json")
    assert "valid JSON" in response.content.decode()
    assert not ImportDraft.objects.exists()


def test_file_over_2_mb_is_rejected(client, setup):
    client.force_login(setup["commissioner"])
    response = upload_file(client, setup, b" " * (2 * 1024 * 1024 + 1))
    assert "over 2 MB" in response.content.decode()
    assert not ImportDraft.objects.exists()


def test_upload_creates_a_draft_and_saves_nothing_else(client, setup):
    client.force_login(setup["commissioner"])
    response = upload_fixture(client, setup)
    assert response.status_code == 302 and response["Location"].endswith("/import/review/")
    assert draft(setup).created_by == setup["commissioner"]
    assert league_data_counts(setup["league"]) == (0, 0, 0, 0, 0)

    total = len(draft(setup).proposal["conflicts"])
    page = client.get(url(setup, "review/")).content.decode()
    assert f"0 of {total} conflicts resolved" in page
    assert "338-7" in page


def test_league_home_links_to_import_and_to_a_pending_draft(client, setup):
    client.force_login(setup["commissioner"])
    home = f"/league/{setup['league'].pk}/"
    page = client.get(home).content.decode()
    assert "Import an old backup" in page and "Resume import" not in page
    upload_fixture(client, setup)
    assert "Resume import" in client.get(home).content.decode()
    client.force_login(setup["member"])
    page = client.get(home).content.decode()
    assert "Resume import" not in page and "Import an old backup" not in page


# --- Review ---------------------------------------------------------------------------


def test_suggestion_is_preselected_but_not_counted_until_saved(client, setup):
    client.force_login(setup["commissioner"])
    upload_fixture(client, setup)
    conflict = next(c for c in draft(setup).proposal["conflicts"] if c["type"] == "score")
    page = client.get(url(setup, "review/")).content.decode()
    assert f'value="{conflict["suggested"]}" id="{conflict["id"]}-' in page
    assert draft(setup).resolutions == {}

    client.post(url(setup, "resolve/"), {"conflict": conflict["id"], "choice": conflict["suggested"]})
    assert draft(setup).resolutions == {conflict["id"]: {"choice": conflict["suggested"]}}
    total = len(draft(setup).proposal["conflicts"])
    assert f"1 of {total} conflicts resolved" in client.get(url(setup, "review/")).content.decode()


def test_invalid_choice_is_not_saved(client, setup):
    client.force_login(setup["commissioner"])
    upload_fixture(client, setup)
    conflict = draft(setup).proposal["conflicts"][0]
    response = client.post(url(setup, "resolve/"), {"conflict": conflict["id"], "choice": "bogus"}, follow=True)
    assert "Pick one of the options" in response.content.decode()
    assert draft(setup).resolutions == {}


def test_typed_score_is_saved(client, setup):
    client.force_login(setup["commissioner"])
    upload_fixture(client, setup)
    conflict = next(c for c in draft(setup).proposal["conflicts"] if c["type"] == "score")
    client.post(url(setup, "resolve/"),
                {"conflict": conflict["id"], "choice": "custom", "team_score": "3", "opponent_score": "38"})
    assert draft(setup).resolutions[conflict["id"]] == {"choice": "custom", "team_score": 3, "opponent_score": 38}


def test_season_wide_acknowledge_resolves_that_seasons_missing_scores(client, setup):
    client.force_login(setup["commissioner"])
    upload_fixture(client, setup)
    client.post(url(setup, "seasons/1/acknowledge/"))
    conflicts = draft(setup).proposal["conflicts"]
    missing = {c["id"] for c in conflicts if c["type"] == "missing_score" and c["season"] == 1}
    assert missing and set(draft(setup).resolutions) == missing


def test_confirm_is_refused_until_every_conflict_is_resolved(client, setup):
    client.force_login(setup["commissioner"])
    upload_fixture(client, setup)
    conflicts = draft(setup).proposal["conflicts"]
    for conflict in conflicts[:-1]:
        client.post(url(setup, "resolve/"), {"conflict": conflict["id"], "choice": conflict["suggested"] or "new"})
    response = client.post(url(setup, "confirm/"), follow=True)
    assert "Not resolved yet" in response.content.decode()
    assert league_data_counts(setup["league"]) == (0, 0, 0, 0, 0)
    assert ImportDraft.objects.filter(league=setup["league"]).exists()


# --- Confirm --------------------------------------------------------------------------


def fixture_season_sums():
    """Per (player name, season name): summed catalog values straight from the fixture file."""
    sums = defaultdict(Counter)
    for player in json.loads(FIXTURE.read_text())["players"]:
        for season in player["seasons"]:
            for game in season["games"]:
                for section in ("offense", "defense"):
                    for old_key, value in game[section].items():
                        key = catalog.LEGACY_KEYS[old_key]
                        if key in catalog.STATS:
                            sums[(player["name"], season["name"])][key] += value
    return sums


def test_confirm_saves_everything_and_season_totals_match_the_fixture(client, setup):
    client.force_login(setup["commissioner"])
    upload_fixture(client, setup)
    resolve_all(client, setup, unplaced="new")
    response = client.post(url(setup, "confirm/"))
    assert response.status_code == 302
    assert not ImportDraft.objects.exists()

    league = setup["league"]
    assert list(Team.objects.filter(league=league).values_list("name", flat=True)) == ["Sharks"]
    seasons = list(Season.objects.filter(team__league=league).order_by("id"))
    assert [(s.name, s.games.count()) for s in seasons] == [("01-02", 10), ("02-03 Season", 7)]
    assert Player.objects.filter(league=league).count() == 5

    saved = defaultdict(Counter)
    for line in StatLine.objects.filter(player__league=league).select_related("player", "game__season"):
        saved[(line.player.name, line.game.season.name)].update(line.values)
    expected = fixture_season_sums()
    for key in expected:
        assert +saved[key] == +expected[key], key

    blake = Player.objects.get(league=league, name="Blake")
    assert (blake.primary_position, blake.height, blake.weight) == ("HB", "5'10\"", 160)
    dnp = StatLine.objects.get(player__name="Drew", game__season=seasons[0], game__opponent="Bengals")
    assert (dnp.did_not_play, dnp.values) == (True, {})
    bills = Game.objects.get(season=seasons[0], order=7)
    assert (bills.opponent, bills.team_score, bills.opponent_score, bills.result) == ("Bills", 7, 38, "L")
    colts = Game.objects.get(season=seasons[1], order=1)
    assert (colts.team_score, colts.recorded_result, colts.result) == (None, "L", "L")
    raiders = Game.objects.get(season=seasons[0], order=1)
    assert raiders.stat_lines.filter(player__name="Casey").exists()


def test_failure_partway_through_confirm_rolls_everything_back(client, setup, monkeypatch):
    client.force_login(setup["commissioner"])
    upload_fixture(client, setup)
    resolve_all(client, setup)

    real_save, calls = StatLine.save, []

    def failing_save(self, *args, **kwargs):
        calls.append(1)
        if len(calls) == 20:
            raise IntegrityError("injected")
        return real_save(self, *args, **kwargs)

    monkeypatch.setattr(StatLine, "save", failing_save)
    response = client.post(url(setup, "confirm/"), follow=True)
    assert "Nothing was saved" in response.content.decode()
    assert league_data_counts(setup["league"]) == (0, 0, 0, 0, 0)
    assert ImportDraft.objects.filter(league=setup["league"]).exists()


def test_import_into_a_league_with_players_needs_explicit_confirmation(client, setup):
    league = setup["league"]
    Player.objects.create(league=league, name="Zed", primary_position="QB")
    client.force_login(setup["commissioner"])
    upload_fixture(client, setup)
    assert "already has 1 player" in client.get(url(setup, "review/")).content.decode()
    resolve_all(client, setup)

    response = client.post(url(setup, "confirm/"), follow=True)
    assert "Tick the box" in response.content.decode()
    assert Player.objects.filter(league=league).count() == 1

    client.post(url(setup, "confirm/"), {"add_alongside": "yes"})
    assert Player.objects.filter(league=league).count() == 6
    assert not ImportDraft.objects.exists()


def test_reimporting_the_same_seasons_fails_cleanly_instead_of_merging(client, setup):
    client.force_login(setup["commissioner"])
    upload_fixture(client, setup)
    resolve_all(client, setup)
    client.post(url(setup, "confirm/"))
    before = league_data_counts(setup["league"])

    upload_fixture(client, setup)
    resolve_all(client, setup)
    response = client.post(url(setup, "confirm/"), {"add_alongside": "yes"}, follow=True)
    assert "Nothing was saved" in response.content.decode()
    assert league_data_counts(setup["league"]) == before


def test_discard_deletes_the_draft(client, setup):
    client.force_login(setup["commissioner"])
    upload_fixture(client, setup)
    client.post(url(setup, "discard/"))
    assert not ImportDraft.objects.exists()
