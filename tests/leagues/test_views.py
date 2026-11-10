"""League, team, season, player, and member management (U5: R7-R13)."""

import re

import pytest

from accounts import tokens
from accounts.models import AccountToken
from leagues.models import League, Membership, Player, Season, Team
from stats.models import StatLine


@pytest.fixture
def setup(make_user, make_league):
    commissioner = make_user("comm")
    jordan = make_user("jordan")
    blake = make_user("blake")
    built = make_league(
        "Lakeside",
        commissioner,
        members=[(jordan, "Jordan"), (blake, "Blake")],
        players=("Jordan", "Blake", "Casey"),
    )
    built.update(commissioner=commissioner, jordan=jordan, blake=blake)
    return built


def url(setup, path=""):
    return f"/league/{setup['league'].pk}/{path}"


def link_in(response):
    match = re.search(r'value="(https?://[^"]+)"', response.content.decode())
    assert match, "link not shown"
    return match.group(1).replace("http://testserver", "")


# --- Creating a league and landing after sign-in (R7, R9) ---------------------------


def test_creating_a_league_makes_the_creator_its_commissioner(client, make_user):
    creator = make_user("creator")
    other = make_user("other")
    client.force_login(creator)
    response = client.post("/leagues/new/", {"name": "Harbor League"})
    league = League.objects.get(name="Harbor League")
    assert response.status_code == 302
    assert response["Location"] == f"/league/{league.pk}/"
    assert Membership.objects.get(league=league, user=creator).is_commissioner

    client.force_login(other)
    assert f"/league/{league.pk}/" not in client.get("/").content.decode()
    assert client.get(f"/league/{league.pk}/").status_code == 404


def test_one_league_user_lands_on_that_leagues_home(client, setup):
    client.force_login(setup["jordan"])
    response = client.get("/")
    assert response.status_code == 302
    assert response["Location"] == url(setup)


def test_two_league_user_sees_the_league_list(client, setup, make_league, make_user):
    other = make_league("Ridgeway", make_user("other-comm"))["league"]
    Membership.objects.create(league=other, user=setup["jordan"])
    client.force_login(setup["jordan"])
    response = client.get("/")
    assert response.status_code == 200
    page = response.content.decode()
    assert f'href="/league/{setup["league"].pk}/"' in page
    assert f'href="/league/{other.pk}/"' in page


def test_user_with_no_league_sees_create_a_league(client, make_user):
    client.force_login(make_user("loner"))
    response = client.get("/")
    assert response.status_code == 200
    assert "Create a league" in response.content.decode()


# --- League home ---------------------------------------------------------------------


def test_new_league_shows_the_setup_checklist(client, make_user):
    creator = make_user("creator")
    client.force_login(creator)
    client.post("/leagues/new/", {"name": "Fresh League"})
    league = League.objects.get(name="Fresh League")
    page = client.get(f"/league/{league.pk}/").content.decode()
    for step in ("Add a team", "Add a season", "Add players", "Invite members"):
        assert step in page


def test_league_home_defaults_to_the_most_recent_season_with_a_switcher(client, setup):
    newer = Season.objects.create(team=setup["team"], name="02-03")
    client.force_login(setup["jordan"])
    page = client.get(url(setup)).content.decode()
    assert "02-03" in page
    assert f"?season={setup['season'].pk}" in page

    page = client.get(url(setup) + f"?season={setup['season'].pk}").content.decode()
    assert f"?season={newer.pk}" in page


def test_unlinked_member_sees_ask_your_commissioner_and_no_entry_links(client, setup, make_user):
    loner = make_user("unlinked")
    Membership.objects.create(league=setup["league"], user=loner)
    client.force_login(loner)
    page = client.get(url(setup)).content.decode()
    assert "Ask your commissioner to link you to a player" in page
    assert "/stats/" not in page
    assert "My player" not in page


def test_member_home_shows_next_game_without_stats(client, setup):
    client.force_login(setup["jordan"])
    page = client.get(url(setup)).content.decode()
    game, player = setup["game"], setup["players"]["Jordan"]
    assert f"/games/{game.pk}/stats/{player.pk}/" in page


# --- Roster and players (R11, R12) ---------------------------------------------------------

FULL_PLAYER = {
    "name": "Dana",
    "overall": "84",
    "primary_position": "WR",
    "secondary_position": "CB",
    "height": "6’2”",
    "weight": "205",
    "super_bowl_wins": "1",
    "status": "Active",
    "retirement_year": "",
    "awards": "MVP, OPOY",
}


def test_commissioner_adds_a_player_with_every_field(client, setup):
    client.force_login(setup["commissioner"])
    data = dict(FULL_PLAYER, team=setup["team"].pk)
    response = client.post(url(setup, "players/new/"), data)
    assert response.status_code == 302
    dana = Player.objects.get(name="Dana")
    assert dana.league == setup["league"]
    assert dana.team == setup["team"]
    assert (dana.overall, dana.primary_position, dana.secondary_position) == (84, "WR", "CB")
    assert (dana.height, dana.weight, dana.super_bowl_wins) == ("6'2\"", 205, 1)
    assert dana.awards == ["MVP", "OPOY"]
    assert "Dana" in client.get(url(setup, "roster/")).content.decode()


def test_member_cannot_add_players(client, setup):
    client.force_login(setup["jordan"])
    assert client.post(url(setup, "players/new/"), dict(FULL_PLAYER)).status_code == 403
    assert not Player.objects.filter(name="Dana").exists()


def test_member_edits_own_bio_and_gets_403_on_another_player(client, setup):
    jordan, blake = setup["players"]["Jordan"], setup["players"]["Blake"]
    client.force_login(setup["jordan"])
    bio = {"name": "Jordan", "overall": "90", "primary_position": "WR", "secondary_position": "",
           "height": "6'0\"", "weight": "190"}
    response = client.post(url(setup, f"players/{jordan.pk}/edit/"), bio)
    assert response.status_code == 302
    jordan.refresh_from_db()
    assert jordan.overall == 90

    assert client.get(url(setup, f"players/{blake.pk}/edit/")).status_code == 403
    assert client.post(url(setup, f"players/{blake.pk}/edit/"), bio).status_code == 403


def test_member_bio_form_cannot_change_commissioner_fields(client, setup):
    jordan = setup["players"]["Jordan"]
    client.force_login(setup["jordan"])
    client.post(
        url(setup, f"players/{jordan.pk}/edit/"),
        {"name": "Jordan", "primary_position": "WR", "status": "Retired", "super_bowl_wins": "9", "awards": "MVP"},
    )
    jordan.refresh_from_db()
    assert (jordan.status, jordan.super_bowl_wins, jordan.awards) == ("Active", 0, [])


def test_retired_player_stays_on_roster_with_badge(client, setup):
    casey = setup["players"]["Casey"]
    client.force_login(setup["commissioner"])
    data = dict(FULL_PLAYER, name="Casey", status="Retired", retirement_year="2031", awards="")
    client.post(url(setup, f"players/{casey.pk}/edit/"), data)
    casey.refresh_from_db()
    assert (casey.status, casey.retirement_year) == ("Retired", 2031)
    page = client.get(url(setup, "roster/")).content.decode()
    assert "Casey" in page
    assert "badge--retired" in page


def test_roster_search_sort_and_count(client, setup):
    other_team = Team.objects.create(league=setup["league"], name="Aardvarks")
    Player.objects.create(league=setup["league"], team=other_team, name="Zed", primary_position="QB")
    client.force_login(setup["blake"])

    page = client.get(url(setup, "roster/")).content.decode()
    assert "4 players" in page
    assert page.index("Blake") < page.index("Casey") < page.index("Zed")

    page = client.get(url(setup, "roster/?sort=team")).content.decode()
    assert page.index("Zed") < page.index("Blake")

    page = client.get(url(setup, "roster/?sort=position")).content.decode()
    assert page.index("Zed") < page.index("Blake")  # QB before WR

    page = client.get(url(setup, "roster/?q=cas")).content.decode()
    assert "1 player" in page
    assert "Casey" in page and "Blake" not in page


# --- Teams and seasons (R8, R13) ------------------------------------------------------------


def test_commissioner_adds_a_team_and_a_season(client, setup):
    client.force_login(setup["commissioner"])
    client.post(url(setup, "teams/"), {"name": "Pelicans"})
    team = Team.objects.get(league=setup["league"], name="Pelicans")
    client.post(url(setup, "seasons/"), {"team": team.pk, "name": "05-06"})
    assert Season.objects.filter(team=team, name="05-06").exists()


def test_duplicate_team_name_shows_an_error(client, setup):
    client.force_login(setup["commissioner"])
    response = client.post(url(setup, "teams/"), {"name": setup["team"].name})
    assert response.status_code == 200
    assert "already" in response.content.decode()


def test_member_cannot_add_teams_or_seasons(client, setup):
    client.force_login(setup["jordan"])
    assert client.post(url(setup, "teams/"), {"name": "Pelicans"}).status_code == 403
    assert client.post(url(setup, "seasons/"), {"team": setup["team"].pk, "name": "x"}).status_code == 403


def test_commissioner_renames_a_team_and_a_season(client, setup):
    client.force_login(setup["commissioner"])
    team, season = setup["team"], setup["season"]
    response = client.post(url(setup, f"teams/{team.pk}/edit/"), {"name": "Pelicans"})
    assert response.status_code == 302
    team.refresh_from_db()
    assert team.name == "Pelicans"
    response = client.post(url(setup, f"seasons/{season.pk}/edit/"), {"team": team.pk, "name": "05-06"})
    assert response.status_code == 302
    season.refresh_from_db()
    assert season.name == "05-06"


def test_member_cannot_rename_teams_or_seasons(client, setup):
    client.force_login(setup["jordan"])
    team, season = setup["team"], setup["season"]
    assert client.get(url(setup, f"teams/{team.pk}/edit/")).status_code == 403
    assert client.post(url(setup, f"teams/{team.pk}/edit/"), {"name": "Pelicans"}).status_code == 403
    assert client.get(url(setup, f"seasons/{season.pk}/edit/")).status_code == 403
    edit = client.post(url(setup, f"seasons/{season.pk}/edit/"), {"team": team.pk, "name": "05-06"})
    assert edit.status_code == 403
    team.refresh_from_db()
    season.refresh_from_db()
    assert (team.name, season.name) == ("Lakeside Team", "01-02")


def test_renaming_a_team_to_a_duplicate_name_shows_an_error(client, setup):
    other = Team.objects.create(league=setup["league"], name="Pelicans")
    client.force_login(setup["commissioner"])
    response = client.post(url(setup, f"teams/{other.pk}/edit/"), {"name": setup["team"].name})
    assert response.status_code == 200
    assert "already" in response.content.decode()
    other.refresh_from_db()
    assert other.name == "Pelicans"


# --- Members (R10, R2, R4) ---------------------------------------------------------------


def test_members_page_is_commissioner_only(client, setup):
    client.force_login(setup["jordan"])
    assert client.get(url(setup, "members/")).status_code == 403
    client.force_login(setup["commissioner"])
    page = client.get(url(setup, "members/")).content.decode()
    assert "jordan" in page and "Jordan" in page


def test_linking_a_member_to_an_already_linked_player_shows_an_error(client, setup, make_user):
    newcomer = make_user("newcomer")
    membership = Membership.objects.create(league=setup["league"], user=newcomer)
    client.force_login(setup["commissioner"])
    response = client.post(
        url(setup, f"members/{newcomer.pk}/link/"), {"player": setup["players"]["Jordan"].pk}
    )
    assert response.status_code == 200
    assert "already linked" in response.content.decode()
    membership.refresh_from_db()
    assert membership.player is None


def test_link_and_unlink_a_member(client, setup, make_user):
    newcomer = make_user("newcomer")
    membership = Membership.objects.create(league=setup["league"], user=newcomer)
    client.force_login(setup["commissioner"])
    client.post(url(setup, f"members/{newcomer.pk}/link/"), {"player": setup["players"]["Casey"].pk})
    membership.refresh_from_db()
    assert membership.player == setup["players"]["Casey"]
    client.post(url(setup, f"members/{newcomer.pk}/unlink/"))
    membership.refresh_from_db()
    assert membership.player is None


def test_removing_a_member_keeps_the_player_and_its_stats_unclaimed(client, setup):
    jordan = setup["players"]["Jordan"]
    StatLine.objects.create(player=jordan, game=setup["game"], values={"receptions": 3})
    client.force_login(setup["commissioner"])
    response = client.post(url(setup, f"members/{setup['jordan'].pk}/remove/"))
    assert response.status_code == 302
    assert not Membership.objects.filter(league=setup["league"], user=setup["jordan"]).exists()
    assert Player.objects.filter(pk=jordan.pk).exists()
    assert StatLine.objects.filter(player=jordan).count() == 1
    assert not Membership.objects.filter(player=jordan).exists()


def test_commissioner_cannot_be_removed_from_their_league(client, setup):
    commissioner = setup["commissioner"]
    client.force_login(commissioner)
    response = client.post(url(setup, f"members/{commissioner.pk}/remove/"), follow=True)
    assert "can&#x27;t be removed" in response.content.decode()
    assert Membership.objects.filter(league=setup["league"], user=commissioner, is_commissioner=True).exists()


def test_member_cannot_link_or_remove(client, setup):
    client.force_login(setup["jordan"])
    blake = setup["blake"]
    assert client.post(url(setup, f"members/{blake.pk}/unlink/")).status_code == 403
    assert client.post(url(setup, f"members/{blake.pk}/remove/")).status_code == 403


def test_members_page_lists_pending_invites(client, setup):
    tokens.create_invite(setup["league"], created_by=setup["commissioner"], invitee_name="Riley")
    client.force_login(setup["commissioner"])
    page = client.get(url(setup, "members/")).content.decode()
    assert "Pending invites" in page
    assert "Riley" in page


def test_revoking_a_pending_invite_makes_its_link_fail(client, setup):
    raw = tokens.create_invite(setup["league"], created_by=setup["commissioner"], invitee_name="Riley")
    invite = AccountToken.objects.get(purpose=AccountToken.Purpose.INVITE)
    client.force_login(setup["commissioner"])
    response = client.post(url(setup, f"invites/{invite.pk}/revoke/"))
    assert response.status_code == 302
    client.logout()
    assert "revoked" in client.get(f"/invite/{raw}/").content.decode()


def test_reissuing_revokes_the_old_link_and_shows_a_working_new_one_once(client, setup):
    old_raw = tokens.create_invite(setup["league"], created_by=setup["commissioner"], invitee_name="Riley")
    invite = AccountToken.objects.get(purpose=AccountToken.Purpose.INVITE)
    client.force_login(setup["commissioner"])
    response = client.post(url(setup, f"invites/{invite.pk}/reissue/"))
    assert response.status_code == 200
    assert "no-store" in response["Cache-Control"]
    new_link = link_in(response)
    assert "won't be shown again" in response.content.decode()
    assert AccountToken.objects.get(pk=invite.pk).voided_at is not None
    assert AccountToken.objects.filter(invitee_name="Riley", voided_at__isnull=True).count() == 1

    client.logout()
    assert "revoked" in client.get(f"/invite/{old_raw}/").content.decode()
    assert "Create account and join" in client.get(new_link).content.decode()


def test_invite_from_another_league_cannot_be_revoked_through_this_league(client, setup, make_league, make_user):
    other_comm = make_user("other-comm")
    other = make_league("Ridgeway", other_comm)["league"]
    tokens.create_invite(other, created_by=other_comm)
    invite = AccountToken.objects.get(league=other)
    client.force_login(setup["commissioner"])
    assert client.post(url(setup, f"invites/{invite.pk}/revoke/")).status_code == 404


def test_link_page_done_goes_back_to_members(client, setup):
    client.force_login(setup["commissioner"])
    response = client.post(url(setup, "invites/new/"), {"invitee_name": "Riley"})
    assert f'href="{url(setup, "members/")}"' in response.content.decode()


# --- Nav ------------------------------------------------------------------------------------


def test_nav_links_and_members_link_only_for_commissioner(client, setup):
    client.force_login(setup["jordan"])
    page = client.get(url(setup)).content.decode()
    assert f'href="{url(setup, "roster/")}"' in page
    assert f'href="{url(setup, "seasons/")}"' in page
    assert f'href="{url(setup, "members/")}"' not in page
    client.force_login(setup["commissioner"])
    assert f'href="{url(setup, "members/")}"' in client.get(url(setup)).content.decode()


# --- Deleting teams, seasons, and players --------------------------------------------


def test_team_with_no_seasons_can_be_deleted_and_its_players_keep_going(client, setup):
    extra = Team.objects.create(league=setup["league"], name="Oops Team")
    casey = setup["players"]["Casey"]
    casey.team = extra
    casey.save()
    client.force_login(setup["commissioner"])
    assert client.get(url(setup, f"teams/{extra.pk}/delete/")).status_code == 200
    response = client.post(url(setup, f"teams/{extra.pk}/delete/"))
    assert response.status_code == 302
    assert not Team.objects.filter(pk=extra.pk).exists()
    casey.refresh_from_db()
    assert casey.team is None


def test_team_with_seasons_cannot_be_deleted(client, setup):
    team, season = setup["team"], setup["season"]
    client.force_login(setup["commissioner"])
    page = client.get(url(setup, f"teams/{team.pk}/delete/")).content.decode().replace("&#x27;", "'")
    assert season.name in page and "can't be deleted" in page
    response = client.post(url(setup, f"teams/{team.pk}/delete/"), follow=True)
    assert "can't be deleted" in response.content.decode().replace("&#x27;", "'")
    assert Team.objects.filter(pk=team.pk).exists()
    assert Season.objects.filter(pk=season.pk).exists()


def test_deleting_a_season_removes_its_games_and_stat_lines_after_showing_counts(client, setup):
    season, game = setup["season"], setup["game"]
    StatLine.objects.create(player=setup["players"]["Jordan"], game=game, values={"receptions": 3})
    StatLine.objects.create(player=setup["players"]["Blake"], game=game, did_not_play=True)
    client.force_login(setup["commissioner"])
    page = client.get(url(setup, f"seasons/{season.pk}/delete/")).content.decode()
    assert "1 game" in page and "2 stat lines" in page
    response = client.post(url(setup, f"seasons/{season.pk}/delete/"))
    assert response.status_code == 302
    assert not Season.objects.filter(pk=season.pk).exists()
    assert not StatLine.objects.filter(game_id=game.pk).exists()
    assert Player.objects.filter(pk=setup["players"]["Jordan"].pk).exists()


def test_after_deleting_its_seasons_a_team_can_be_deleted(client, setup):
    team, season = setup["team"], setup["season"]
    client.force_login(setup["commissioner"])
    client.post(url(setup, f"seasons/{season.pk}/delete/"))
    client.post(url(setup, f"teams/{team.pk}/delete/"))
    assert not Team.objects.filter(pk=team.pk).exists()


def test_deleting_a_player_removes_their_stat_lines_and_unlinks_the_member(client, setup):
    jordan = setup["players"]["Jordan"]
    StatLine.objects.create(player=jordan, game=setup["game"], values={"receptions": 3})
    client.force_login(setup["commissioner"])
    page = client.get(url(setup, f"players/{jordan.pk}/delete/")).content.decode()
    assert "1 stat line" in page
    response = client.post(url(setup, f"players/{jordan.pk}/delete/"))
    assert response.status_code == 302
    assert not Player.objects.filter(pk=jordan.pk).exists()
    assert not StatLine.objects.filter(player_id=jordan.pk).exists()
    membership = Membership.objects.get(league=setup["league"], user=setup["jordan"])
    assert membership.player is None


def test_member_cannot_delete_teams_seasons_or_players(client, setup):
    extra = Team.objects.create(league=setup["league"], name="Oops Team")
    client.force_login(setup["jordan"])
    for path in (
        f"teams/{extra.pk}/delete/",
        f"seasons/{setup['season'].pk}/delete/",
        f"players/{setup['players']['Blake'].pk}/delete/",
        f"players/{setup['players']['Jordan'].pk}/delete/",
    ):
        assert client.get(url(setup, path)).status_code == 403
        assert client.post(url(setup, path)).status_code == 403
    assert Team.objects.filter(pk=extra.pk).exists()
    assert Season.objects.filter(pk=setup["season"].pk).exists()
    assert Player.objects.filter(league=setup["league"]).count() == 3


def test_deleting_through_another_leagues_url_is_404(client, setup, make_league, make_user):
    other = make_league("Elsewhere", make_user("othercomm"), players=("Remote",))
    other_team = Team.objects.create(league=other["league"], name="Spare")
    client.force_login(setup["commissioner"])
    for path in (
        f"teams/{other_team.pk}/delete/",
        f"seasons/{other['season'].pk}/delete/",
        f"players/{other['players']['Remote'].pk}/delete/",
    ):
        assert client.post(url(setup, path)).status_code == 404
    assert Team.objects.filter(pk=other_team.pk).exists()
    assert Season.objects.filter(pk=other["season"].pk).exists()
    assert Player.objects.filter(pk=other["players"]["Remote"].pk).exists()


def test_delete_links_shown_to_the_commissioner_only(client, setup):
    team, season, jordan = setup["team"], setup["season"], setup["players"]["Jordan"]
    client.force_login(setup["commissioner"])
    assert f"teams/{team.pk}/delete/" in client.get(url(setup, "teams/")).content.decode()
    assert f"seasons/{season.pk}/delete/" in client.get(url(setup, "seasons/")).content.decode()
    assert f"players/{jordan.pk}/delete/" in client.get(url(setup, f"players/{jordan.pk}/edit/")).content.decode()
    client.force_login(setup["jordan"])
    assert "/delete/" not in client.get(url(setup, "seasons/")).content.decode()
    assert "/delete/" not in client.get(url(setup, f"players/{jordan.pk}/edit/")).content.decode()


def test_header_new_league_link_shows_for_the_commissioner_only(client, setup):
    client.force_login(setup["commissioner"])
    assert 'href="/leagues/new/"' in client.get(url(setup)).content.decode()
    client.force_login(setup["jordan"])
    assert 'href="/leagues/new/"' not in client.get(url(setup)).content.decode()


# --- League settings, deleting a league, and co-commissioners ----------------------------


def make_commissioner(setup, user):
    Membership.objects.filter(league=setup["league"], user=user).update(is_commissioner=True)


def test_commissioner_renames_the_league(client, setup):
    client.force_login(setup["commissioner"])
    response = client.post(url(setup, "settings/"), {"name": "Lakeside Legends"})
    assert response.status_code == 302
    setup["league"].refresh_from_db()
    assert setup["league"].name == "Lakeside Legends"


def test_member_cannot_open_league_settings_or_delete(client, setup):
    client.force_login(setup["jordan"])
    for path in ("settings/", "delete/"):
        assert client.get(url(setup, path)).status_code == 403
        assert client.post(url(setup, path), {"name": "x", "confirm_name": "Lakeside"}).status_code == 403
    assert League.objects.filter(pk=setup["league"].pk, name="Lakeside").exists()


def test_delete_page_shows_what_goes_and_links_to_export(client, setup):
    StatLine.objects.create(player=setup["players"]["Jordan"], game=setup["game"], values={"receptions": 3})
    client.force_login(setup["commissioner"])
    page = client.get(url(setup, "delete/")).content.decode()
    for text in ("1 team", "1 season", "1 game", "3 players", "1 stat line", "3 members"):
        assert text in page
    assert f"/league/{setup['league'].pk}/export/" in page


def test_deleting_a_league_needs_its_exact_name(client, setup):
    client.force_login(setup["commissioner"])
    response = client.post(url(setup, "delete/"), {"confirm_name": "lakeside "})
    assert response.status_code == 200
    assert "Type the league name exactly" in response.content.decode()
    assert League.objects.filter(pk=setup["league"].pk).exists()


def test_deleting_a_league_removes_everything_in_it_but_keeps_accounts(client, setup):
    from django.contrib.auth import get_user_model

    league = setup["league"]
    StatLine.objects.create(player=setup["players"]["Jordan"], game=setup["game"], values={"receptions": 3})
    tokens.create_invite(league, created_by=setup["commissioner"], invitee_name="Riley")
    client.force_login(setup["commissioner"])
    response = client.post(url(setup, "delete/"), {"confirm_name": "Lakeside"})
    assert response.status_code == 302 and response.url == "/"
    assert not League.objects.filter(pk=league.pk).exists()
    assert not Team.objects.filter(league_id=league.pk).exists()
    assert not Player.objects.filter(league_id=league.pk).exists()
    assert not Membership.objects.filter(league_id=league.pk).exists()
    assert not StatLine.objects.filter(game_id=setup["game"].pk).exists()
    assert not AccountToken.objects.filter(league_id=league.pk).exists()
    assert get_user_model().objects.filter(pk__in=[setup["commissioner"].pk, setup["jordan"].pk]).count() == 2


def test_another_leagues_commissioner_cannot_delete_this_league(client, setup, make_league, make_user):
    other_comm = make_user("othercomm")
    make_league("Elsewhere", other_comm)
    client.force_login(other_comm)
    assert client.post(url(setup, "delete/"), {"confirm_name": "Lakeside"}).status_code == 404
    assert League.objects.filter(pk=setup["league"].pk).exists()


def test_commissioner_makes_a_member_a_co_commissioner(client, setup):
    client.force_login(setup["commissioner"])
    response = client.post(url(setup, f"members/{setup['jordan'].pk}/commissioner/"))
    assert response.status_code == 302
    assert Membership.objects.get(league=setup["league"], user=setup["jordan"]).is_commissioner
    client.force_login(setup["jordan"])
    assert client.get(url(setup, "members/")).status_code == 200


def test_member_cannot_make_anyone_a_commissioner(client, setup):
    client.force_login(setup["jordan"])
    assert client.post(url(setup, f"members/{setup['jordan'].pk}/commissioner/")).status_code == 403
    assert client.post(url(setup, f"members/{setup['blake'].pk}/commissioner/")).status_code == 403
    assert not Membership.objects.filter(league=setup["league"], is_commissioner=True).exclude(
        user=setup["commissioner"]
    ).exists()


def test_the_last_commissioner_cannot_step_down(client, setup):
    comm = setup["commissioner"]
    client.force_login(comm)
    response = client.post(url(setup, f"members/{comm.pk}/commissioner/remove/"), follow=True)
    assert "last commissioner" in response.content.decode()
    assert Membership.objects.get(league=setup["league"], user=comm).is_commissioner


def test_handing_off_make_another_commissioner_then_step_down(client, setup):
    comm, jordan = setup["commissioner"], setup["jordan"]
    client.force_login(comm)
    client.post(url(setup, f"members/{jordan.pk}/commissioner/"))
    response = client.post(url(setup, f"members/{comm.pk}/commissioner/remove/"))
    assert response.status_code == 302 and response.url == url(setup)
    assert not Membership.objects.get(league=setup["league"], user=comm).is_commissioner
    assert Membership.objects.get(league=setup["league"], user=jordan).is_commissioner
    assert client.get(url(setup, "members/")).status_code == 403


def test_a_commissioner_can_remove_another_commissioners_role(client, setup):
    make_commissioner(setup, setup["jordan"])
    client.force_login(setup["commissioner"])
    client.post(url(setup, f"members/{setup['jordan'].pk}/commissioner/remove/"))
    assert not Membership.objects.get(league=setup["league"], user=setup["jordan"]).is_commissioner


def test_a_commissioner_cannot_be_removed_until_their_role_is(client, setup):
    make_commissioner(setup, setup["jordan"])
    client.force_login(setup["commissioner"])
    client.post(url(setup, f"members/{setup['jordan'].pk}/remove/"))
    assert Membership.objects.filter(league=setup["league"], user=setup["jordan"]).exists()


def test_commissioner_cannot_get_a_reset_link_for_a_co_commissioner(client, setup):
    make_commissioner(setup, setup["jordan"])
    client.force_login(setup["commissioner"])
    assert client.post(url(setup, f"members/{setup['jordan'].pk}/reset-link/")).status_code == 403
    assert client.post(url(setup, f"members/{setup['blake'].pk}/reset-link/")).status_code == 200


def test_members_page_shows_commissioner_role_controls(client, setup):
    client.force_login(setup["commissioner"])
    page = client.get(url(setup, "members/")).content.decode()
    assert f"members/{setup['jordan'].pk}/commissioner/" in page
    assert f"members/{setup['commissioner'].pk}/commissioner/remove/" not in page
    assert url(setup, "settings/") in page
