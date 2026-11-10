"""League export and re-import as a native league file (U9: R27)."""

import json

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from leagues.models import Game, League, Membership, Player, Season, Team
from stats import aggregate
from stats.models import StatLine

from .test_import_flow import FIXTURE, resolve_all, upload_fixture

NATIVE_IMPORT_URL = "/leagues/import/"


@pytest.fixture
def source(client, make_user):
    """A league populated through the legacy import UI, plus a few hand-entered extras."""
    commissioner = make_user("zq-commish-account", email="zq-commish@example.test")
    member = make_user("zq-member-account", email="zq-member@example.test")
    league = League.objects.create(name="Lakeside Legends")
    Membership.objects.create(league=league, user=commissioner, is_commissioner=True)
    setup = {"league": league, "commissioner": commissioner, "member": member}

    client.force_login(commissioner)
    upload_fixture(client, setup)
    resolve_all(client, setup, unplaced="new")
    assert client.post(f"/league/{league.pk}/import/confirm/").status_code == 302

    # Fields the legacy fixture doesn't exercise: retirement, awards, a playoff game,
    # half sacks, negative yards, a second team, and a player with no team.
    first = Player.objects.filter(league=league).order_by("id").first()
    first.status, first.retirement_year, first.awards, first.super_bowl_wins = "Retired", 2031, ["MVP", "OPOY"], 2
    first.overall = 88
    first.save()
    season = Season.objects.filter(team__league=league).order_by("id").first()
    playoff = Game.objects.create(
        season=season, order=99, opponent="Chiefs", team_score=27, opponent_score=24, is_playoff=True
    )
    StatLine.objects.create(player=first, game=playoff, values={"sacks": 1.5, "rushing_yards": -3})
    Team.objects.create(league=league, name="Harbor Hawks")
    Player.objects.create(league=league, name="Free Agent Fay", primary_position="K")

    Membership.objects.create(league=league, user=member, player=first)
    return setup


def export(client, league):
    return client.get(f"/league/{league.pk}/export/")


def post_league_file(client, content, url=NATIVE_IMPORT_URL, field="league_file"):
    return client.post(url, {field: SimpleUploadedFile("league.json", content, content_type="application/json")})


def snapshot(league):
    """Everything a league holds, keyed by names and positions (not database IDs)."""
    seasons = []
    for season in Season.objects.filter(team__league=league).select_related("team").order_by("id"):
        games = [
            (g.order, g.opponent, g.team_score, g.opponent_score, g.recorded_result, g.result, g.is_playoff)
            for g in season.games.all()
        ]
        seasons.append((season.team.name, season.name, games))
    players = [
        (p.name, p.team.name if p.team else None, p.overall, p.primary_position, p.secondary_position,
         p.height, p.weight, p.super_bowl_wins, p.status, p.retirement_year, p.awards)
        for p in Player.objects.filter(league=league).select_related("team").order_by("id")
    ]
    lines = sorted(
        (line.player.name, line.game.season.name, line.game.order, line.game.opponent,
         line.did_not_play, json.dumps(line.values, sort_keys=True))
        for line in StatLine.objects.filter(player__league=league).select_related("player", "game__season")
    )
    totals = {}
    for player in Player.objects.filter(league=league):
        career = aggregate.summarize(player.stat_lines.all())
        totals[(player.name, "career")] = (career.games_played, career.did_not_play, career.totals, career.derived)
        for season in Season.objects.filter(team__league=league):
            summary = aggregate.summarize(player.stat_lines.filter(game__season=season))
            totals[(player.name, season.name)] = (summary.games_played, summary.totals, summary.derived)
    teams = sorted(Team.objects.filter(league=league).values_list("name", flat=True))
    return {"teams": teams, "seasons": seasons, "players": players, "lines": lines, "totals": totals}


def test_round_trip_into_a_fresh_league_is_exact(client, source, make_user):
    league = source["league"]
    client.force_login(source["commissioner"])
    response = export(client, league)
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/json")
    assert response["Content-Disposition"].startswith('attachment; filename="lakeside-legends-')
    assert response["Content-Disposition"].endswith('.json"')
    assert "no-store" in response["Cache-Control"]

    importer = make_user("zq-new-host")
    client.force_login(importer)
    result = post_league_file(client, response.content)
    assert result.status_code == 302

    copy = League.objects.exclude(pk=league.pk).get()
    assert result["Location"] == f"/league/{copy.pk}/"
    assert copy.name == "Lakeside Legends"
    assert list(Membership.objects.filter(league=copy).values_list("user__username", "is_commissioner", "player")) == [
        ("zq-new-host", True, None)
    ]
    original = snapshot(league)
    assert original["lines"] and original["seasons"] and original["players"]
    assert snapshot(copy) == original


def test_export_has_no_account_data(client, source):
    league = source["league"]
    client.force_login(source["commissioner"])
    text = export(client, league).content.decode()
    for user in get_user_model().objects.all():
        assert user.username not in text
        assert user.email not in text
        assert user.password not in text
    lowered = text.lower()
    for word in ("username", "email", "password", "token", "membership", "commissioner", "draft"):
        assert word not in lowered


def test_member_who_is_not_commissioner_gets_403_on_export(client, source):
    client.force_login(source["member"])
    assert export(client, source["league"]).status_code == 403


def test_non_member_gets_404_on_export(client, source, make_user):
    client.force_login(make_user("zq-outsider"))
    assert export(client, source["league"]).status_code == 404


def native_document(client, source):
    client.force_login(source["commissioner"])
    return json.loads(export(client, source["league"]).content)


def test_unknown_version_is_rejected_and_nothing_is_created(client, source):
    document = native_document(client, source)
    document["version"] = 99
    leagues_before = League.objects.count()
    response = post_league_file(client, json.dumps(document).encode())
    assert response.status_code == 200
    assert "version 99" in response.content.decode()
    assert League.objects.count() == leagues_before


def test_bad_data_late_in_the_file_saves_nothing(client, source):
    document = native_document(client, source)
    document["stat_lines"][-1]["values"] = {"solo_tackles": -2}
    counts_before = (League.objects.count(), Player.objects.count(), Game.objects.count(), StatLine.objects.count())
    response = post_league_file(client, json.dumps(document).encode())
    assert response.status_code == 200
    assert "Solo tackles can&#x27;t be negative" in response.content.decode()
    after = (League.objects.count(), Player.objects.count(), Game.objects.count(), StatLine.objects.count())
    assert after == counts_before


def test_file_over_2_mb_is_rejected(client, source):
    client.force_login(source["commissioner"])
    leagues_before = League.objects.count()
    response = post_league_file(client, b" " * (2 * 1024 * 1024 + 1))
    assert "over 2 MB" in response.content.decode()
    assert League.objects.count() == leagues_before


def test_old_backup_on_the_league_file_page_points_to_the_legacy_import(client, source):
    client.force_login(source["commissioner"])
    leagues_before = League.objects.count()
    response = post_league_file(client, FIXTURE.read_bytes())
    assert "old Madden Tracker backup" in response.content.decode()
    assert League.objects.count() == leagues_before


def test_league_file_on_the_legacy_page_points_to_the_league_file_page(client, source):
    document = native_document(client, source)
    url = f"/league/{source['league'].pk}/import/"
    response = post_league_file(client, json.dumps(document).encode(), url=url, field="backup")
    page = response.content.decode()
    assert "Import a league file" in page
    assert NATIVE_IMPORT_URL in page


def test_links_to_export_and_league_file_import(client, source, make_user):
    league = source["league"]
    client.force_login(source["commissioner"])
    assert f"/league/{league.pk}/export/" in client.get(f"/league/{league.pk}/").content.decode()
    assert NATIVE_IMPORT_URL in client.get("/leagues/new/").content.decode()
    client.force_login(make_user("zq-no-leagues"))
    assert NATIVE_IMPORT_URL in client.get("/").content.decode()
    client.force_login(source["member"])
    assert f"/league/{league.pk}/export/" not in client.get(f"/league/{league.pk}/").content.decode()
