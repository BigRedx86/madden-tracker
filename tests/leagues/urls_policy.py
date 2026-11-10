"""Test-only league views that exercise leagues.access the way real views must."""

from django.http import HttpResponse
from django.urls import include, path

from leagues import policy
from leagues.access import get_in_league, league_view, require
from leagues.models import Game, Player, Season


@league_view
def league_home(request, league_id):
    return HttpResponse(f"league {request.league.pk}")


@league_view
def player_detail(request, league_id, player_id):
    player = get_in_league(Player, request.league, pk=player_id)
    return HttpResponse(player.name)


@league_view
def player_bio_edit(request, league_id, player_id):
    player = get_in_league(Player, request.league, pk=player_id)
    require(policy.can_edit_player_bio(request.user, player))
    return HttpResponse("bio editable")


@league_view
def season_detail(request, league_id, season_id):
    season = get_in_league(Season, request.league, pk=season_id)
    return HttpResponse(season.name)


@league_view
def game_detail(request, league_id, game_id):
    game = get_in_league(Game, request.league, pk=game_id)
    return HttpResponse(game.opponent)


@league_view
def game_edit(request, league_id, game_id):
    get_in_league(Game, request.league, pk=game_id)
    require(policy.can_manage_games(request.user, request.league))
    return HttpResponse("game editable")


@league_view
def game_create(request, league_id):
    require(policy.can_manage_games(request.user, request.league))
    return HttpResponse("game creatable")


@league_view
def stat_line_edit(request, league_id, game_id, player_id):
    get_in_league(Game, request.league, pk=game_id)
    player = get_in_league(Player, request.league, pk=player_id)
    require(policy.can_edit_stat_line(request.user, player))
    return HttpResponse("stat line editable")


league_patterns = [
    path("", league_home),
    path("players/<int:player_id>/", player_detail),
    path("players/<int:player_id>/bio/", player_bio_edit),
    path("seasons/<int:season_id>/", season_detail),
    path("games/new/", game_create),
    path("games/<int:game_id>/", game_detail),
    path("games/<int:game_id>/edit/", game_edit),
    path("games/<int:game_id>/stats/<int:player_id>/", stat_line_edit),
]

urlpatterns = [path("league/<int:league_id>/", include(league_patterns))]
