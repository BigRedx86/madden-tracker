"""Permission matrix and league-scoped access (KTD3, R9-R11, R14, R15, AE1, AE7).

The matrix mirrors the "Who can do what" table in the plan. Every later
unit's league views must pass these checks, and the unguarded-URL test
fails if a league URL is registered without leagues.access.league_view.
"""

import pytest

from leagues import policy
from leagues.access import find_unguarded_league_urls
from leagues.models import Membership

URLS = "tests.leagues.urls_policy"


@pytest.fixture
def world(make_user, make_league):
    """League A: commissioner, Jordan (linked to "Jordan"), Blake (linked to "Blake"),
    and an unclaimed player "Casey". League B: its own commissioner and member.
    Plus a site admin who belongs to no league."""
    commissioner = make_user("a-comm")
    jordan = make_user("jordan")
    blake = make_user("blake")
    a = make_league(
        "League A",
        commissioner,
        members=[(jordan, "Jordan"), (blake, "Blake")],
        players=("Jordan", "Blake", "Casey"),
    )
    b_comm = make_user("b-comm")
    b_member = make_user("b-member")
    b = make_league("League B", b_comm, members=[(b_member, "Bravo")])
    site_admin = make_user("owner", is_superuser=True, is_staff=True)
    return {
        "a": a,
        "b": b,
        "users": {
            "site_admin": site_admin,
            "commissioner": commissioner,
            "member_own": jordan,
            "member_other": blake,
            "b_comm": b_comm,
            "b_member": b_member,
        },
    }


# --- The "Who can do what" table ------------------------------------------------
# Player-targeted rows act on Jordan's player: "member_own" is Jordan,
# "member_other" is Blake.

MATRIX = {
    #                      site_admin, commissioner, member_own, member_other
    "view_league": (False, True, True, True),
    "manage_games": (False, True, False, False),
    "edit_stat_line": (False, True, True, False),
    "edit_player_bio": (False, True, True, False),
    "manage_players": (False, True, False, False),
    "invite": (False, True, False, False),
    "manage_accounts": (True, False, False, False),
}
ROLES = ("site_admin", "commissioner", "member_own", "member_other")


def check(action, user, world):
    league = world["a"]["league"]
    player = world["a"]["players"]["Jordan"]
    return {
        "view_league": lambda: policy.can_view_league(user, league),
        "manage_games": lambda: policy.can_manage_games(user, league),
        "edit_stat_line": lambda: policy.can_edit_stat_line(user, player),
        "edit_player_bio": lambda: policy.can_edit_player_bio(user, player),
        "manage_players": lambda: policy.can_manage_players(user, league),
        "invite": lambda: policy.can_invite(user, league),
        "manage_accounts": lambda: policy.can_manage_accounts(user),
    }[action]()


@pytest.mark.parametrize(
    "action, role, expected",
    [(action, role, allowed) for action, row in MATRIX.items() for role, allowed in zip(ROLES, row)],
)
def test_permission_matrix(world, action, role, expected):
    assert check(action, world["users"][role], world) is expected


def test_other_leagues_commissioner_has_no_rights_in_league_a(world):
    outsider = world["users"]["b_comm"]
    for action in MATRIX:
        if action != "manage_accounts":
            assert check(action, outsider, world) is False, action


def test_policy_helpers(world):
    league = world["a"]["league"]
    users = world["users"]
    assert policy.is_commissioner(users["commissioner"], league)
    assert not policy.is_commissioner(users["member_own"], league)
    assert policy.is_site_admin(users["site_admin"])
    assert not policy.is_site_admin(users["commissioner"])
    assert policy.linked_player(users["member_own"], league) == world["a"]["players"]["Jordan"]
    assert policy.linked_player(users["commissioner"], league) is None
    assert policy.linked_player(users["b_member"], league) is None


def test_disabled_site_admin_is_not_a_site_admin(world):
    admin = world["users"]["site_admin"]
    admin.is_active = False
    assert not policy.is_site_admin(admin)


# Reset-link row (R4, KTD11): site admin -> anyone; commissioner -> only members
# whose every league they run, never a site admin; members -> nobody.


def test_site_admin_can_reset_anyone(world):
    users = world["users"]
    for target in ("commissioner", "member_own", "b_member", "b_comm"):
        assert policy.can_generate_reset_link(users["site_admin"], users[target])


def test_commissioner_can_reset_member_whose_only_league_they_run(world):
    users = world["users"]
    assert policy.can_generate_reset_link(users["commissioner"], users["member_own"])


def test_commissioner_cannot_reset_member_who_is_also_in_another_league(world):
    users = world["users"]
    Membership.objects.create(league=world["b"]["league"], user=users["member_own"])
    assert not policy.can_generate_reset_link(users["commissioner"], users["member_own"])


def test_commissioner_of_both_leagues_can_reset_member_of_both(world):
    users = world["users"]
    Membership.objects.create(league=world["b"]["league"], user=users["member_own"])
    Membership.objects.create(league=world["b"]["league"], user=users["commissioner"], is_commissioner=True)
    assert policy.can_generate_reset_link(users["commissioner"], users["member_own"])


def test_commissioner_cannot_reset_a_site_admin_even_in_their_league(world):
    users = world["users"]
    Membership.objects.create(league=world["a"]["league"], user=users["site_admin"])
    assert not policy.can_generate_reset_link(users["commissioner"], users["site_admin"])


def test_commissioner_cannot_reset_someone_outside_their_leagues(world):
    users = world["users"]
    assert not policy.can_generate_reset_link(users["commissioner"], users["b_member"])
    assert not policy.can_generate_reset_link(users["commissioner"], users["site_admin"])


def test_members_cannot_reset_anyone(world):
    users = world["users"]
    assert not policy.can_generate_reset_link(users["member_own"], users["member_other"])


# --- League-scoped views (AE1, AE7) --------------------------------------------------


def a_urls(world):
    a = world["a"]
    league = a["league"].pk
    return {
        "league": f"/league/{league}/",
        "player": f"/league/{league}/players/{a['players']['Jordan'].pk}/",
        "season": f"/league/{league}/seasons/{a['season'].pk}/",
        "game": f"/league/{league}/games/{a['game'].pk}/",
    }


@pytest.mark.urls(URLS)
@pytest.mark.parametrize("page", ["league", "player", "season", "game"])
def test_member_of_another_league_gets_404(client, world, page):
    # Covers AE7.
    client.force_login(world["users"]["b_member"])
    assert client.get(a_urls(world)[page]).status_code == 404


@pytest.mark.urls(URLS)
@pytest.mark.parametrize("page", ["league", "player", "season", "game"])
def test_members_can_view_every_page_in_their_league(client, world, page):
    client.force_login(world["users"]["member_other"])
    assert client.get(a_urls(world)[page]).status_code == 200


@pytest.mark.urls(URLS)
@pytest.mark.parametrize("page", ["league", "player", "season", "game"])
def test_site_admin_who_is_not_a_member_gets_404(client, world, page):
    client.force_login(world["users"]["site_admin"])
    assert client.get(a_urls(world)[page]).status_code == 404


@pytest.mark.urls(URLS)
def test_anonymous_user_is_sent_to_sign_in(client, world):
    response = client.get(a_urls(world)["league"])
    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.urls(URLS)
def test_missing_league_is_404(client, world):
    client.force_login(world["users"]["commissioner"])
    assert client.get("/league/999999/").status_code == 404


def stat_url(world, player_name):
    a = world["a"]
    return f"/league/{a['league'].pk}/games/{a['game'].pk}/stats/{a['players'][player_name].pk}/"


@pytest.mark.urls(URLS)
def test_linked_member_edits_own_stat_line_and_gets_403_on_anothers(client, world):
    # Covers AE1.
    client.force_login(world["users"]["member_own"])
    assert client.get(stat_url(world, "Jordan")).status_code == 200
    assert client.get(stat_url(world, "Blake")).status_code == 403


@pytest.mark.urls(URLS)
def test_commissioner_edits_unclaimed_players_stat_line(client, world):
    client.force_login(world["users"]["commissioner"])
    assert client.get(stat_url(world, "Casey")).status_code == 200


@pytest.mark.urls(URLS)
def test_member_cannot_edit_unclaimed_players_stat_line(client, world):
    client.force_login(world["users"]["member_own"])
    assert client.get(stat_url(world, "Casey")).status_code == 403


@pytest.mark.urls(URLS)
def test_only_commissioner_creates_or_edits_games(client, world):
    a = world["a"]
    edit = f"/league/{a['league'].pk}/games/{a['game'].pk}/edit/"
    create = f"/league/{a['league'].pk}/games/new/"
    client.force_login(world["users"]["member_own"])
    assert client.get(edit).status_code == 403
    assert client.get(create).status_code == 403
    client.force_login(world["users"]["commissioner"])
    assert client.get(edit).status_code == 200
    assert client.get(create).status_code == 200


@pytest.mark.urls(URLS)
def test_bio_edit_follows_the_matrix(client, world):
    a = world["a"]
    url = f"/league/{a['league'].pk}/players/{a['players']['Jordan'].pk}/bio/"
    client.force_login(world["users"]["member_other"])
    assert client.get(url).status_code == 403
    client.force_login(world["users"]["member_own"])
    assert client.get(url).status_code == 200


@pytest.mark.urls(URLS)
def test_foreign_object_id_in_own_league_url_is_404(client, world):
    a, b = world["a"], world["b"]
    league = a["league"].pk
    client.force_login(world["users"]["commissioner"])
    foreign = {
        "player": f"/league/{league}/players/{b['players']['Bravo'].pk}/",
        "season": f"/league/{league}/seasons/{b['season'].pk}/",
        "game": f"/league/{league}/games/{b['game'].pk}/",
        "game_edit": f"/league/{league}/games/{b['game'].pk}/edit/",
        "stat_line": f"/league/{league}/games/{a['game'].pk}/stats/{b['players']['Bravo'].pk}/",
    }
    for name, url in foreign.items():
        assert client.get(url).status_code == 404, name


def test_foreign_team_or_season_id_in_real_edit_urls_is_404(client, world):
    # team_edit and season_edit live only in the real URLconf, not URLS.
    league, b = world["a"]["league"].pk, world["b"]
    client.force_login(world["users"]["commissioner"])
    foreign = {
        "team_edit": f"/league/{league}/teams/{b['team'].pk}/edit/",
        "season_edit": f"/league/{league}/seasons/{b['season'].pk}/edit/",
    }
    for name, url in foreign.items():
        assert client.get(url).status_code == 404, name
        assert client.post(url, {"name": "Taken", "team": world["a"]["team"].pk}).status_code == 404, name
    b["team"].refresh_from_db()
    b["season"].refresh_from_db()
    assert (b["team"].name, b["season"].name) == ("League B Team", "01-02")


def test_view_attaches_league_and_membership(rf, world):
    from leagues.access import league_view

    seen = {}

    @league_view
    def view(request, league_id):
        seen["league"] = request.league
        seen["membership"] = request.membership
        return None

    request = rf.get("/")
    request.user = world["users"]["member_own"]
    view(request, league_id=world["a"]["league"].pk)
    assert seen["league"] == world["a"]["league"]
    assert seen["membership"].player == world["a"]["players"]["Jordan"]


# --- Every league URL is guarded -------------------------------------------------------


def test_every_league_url_in_the_project_uses_the_decorator():
    assert find_unguarded_league_urls("config.urls") == []


def test_test_views_are_all_guarded():
    assert find_unguarded_league_urls(URLS) == []


def test_unguarded_league_urls_are_detected():
    assert sorted(find_unguarded_league_urls("tests.leagues.urls_unguarded")) == [
        "^other/(?P<league_id>\\d+)/$",
        "league/<int:league_id>/roster/",
        "league/new/",
    ]
