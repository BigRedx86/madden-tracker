"""League URLs. Everything under "league/<int:league_id>/" uses @league_view (leagues.access)."""

from django.urls import include, path

from stats import entry_views

from . import game_views, views

app_name = "leagues"

league_patterns = [
    path("", views.league_home, name="league_home"),
    path("settings/", views.league_settings, name="league_settings"),
    path("delete/", views.league_delete, name="league_delete"),
    path("roster/", views.roster, name="roster"),
    path("players/new/", views.player_create, name="player_create"),
    path("players/<int:player_id>/edit/", views.player_edit, name="player_edit"),
    path("players/<int:player_id>/delete/", views.player_delete, name="player_delete"),
    path("teams/", views.teams, name="teams"),
    path("teams/<int:team_id>/edit/", views.team_edit, name="team_edit"),
    path("teams/<int:team_id>/delete/", views.team_delete, name="team_delete"),
    path("seasons/", views.seasons, name="seasons"),
    path("seasons/<int:season_id>/", game_views.season_view, name="season"),
    path("seasons/<int:season_id>/edit/", views.season_edit, name="season_edit"),
    path("seasons/<int:season_id>/delete/", views.season_delete, name="season_delete"),
    path("seasons/<int:season_id>/games/new/", game_views.game_create, name="game_create"),
    path("games/<int:game_id>/", game_views.game_detail, name="game"),
    path("games/<int:game_id>/edit/", game_views.game_edit, name="game_edit"),
    path("games/<int:game_id>/delete/", game_views.game_delete, name="game_delete"),
    path("games/<int:game_id>/move/", game_views.game_move, name="game_move"),
    path("games/<int:game_id>/stats/<int:player_id>/", entry_views.stat_line_edit, name="stat_line"),
    path("members/", views.members, name="members"),
    path("members/<int:user_id>/link/", views.member_link, name="member_link"),
    path("members/<int:user_id>/unlink/", views.member_unlink, name="member_unlink"),
    path("members/<int:user_id>/remove/", views.member_remove, name="member_remove"),
    path("members/<int:user_id>/commissioner/", views.member_make_commissioner, name="member_make_commissioner"),
    path(
        "members/<int:user_id>/commissioner/remove/",
        views.member_remove_commissioner,
        name="member_remove_commissioner",
    ),
    path("invites/<int:invite_id>/revoke/", views.invite_revoke, name="invite_revoke"),
    path("invites/<int:invite_id>/reissue/", views.invite_reissue, name="invite_reissue"),
]

urlpatterns = [
    path("", views.landing, name="home"),
    path("leagues/new/", views.league_create, name="league_create"),
    path("league/<int:league_id>/", include(league_patterns)),
]
