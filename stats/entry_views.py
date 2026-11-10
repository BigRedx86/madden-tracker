"""Stat line entry for one player in one game (U6: R15-R17, AE1, AE6)."""

from django.contrib import messages
from django.shortcuts import redirect, render

from leagues import policy
from leagues.access import get_in_league, league_view, require
from leagues.game_views import season_url
from leagues.models import Game, Player

from .forms import StatLineForm
from .models import StatLine


@league_view
def stat_line_edit(request, league_id, game_id, player_id):
    game = get_in_league(Game, request.league, pk=game_id)
    player = get_in_league(Player, request.league, pk=player_id)
    require(policy.can_edit_stat_line(request.user, player, season=game.season))
    line = StatLine.objects.filter(player=player, game=game).first()

    form = StatLineForm(request.POST or None, line=line)
    if request.method == "POST" and form.is_valid():
        line = line or StatLine(player=player, game=game)
        line.did_not_play = form.cleaned_data["did_not_play"]
        line.values = form.cleaned_data["values"]
        line.save()
        messages.success(request, f"Saved {player.name}'s line for {game}.")
        # Back to the game list, where the next game to enter is highlighted.
        return redirect(season_url(game.season, player))

    context = form.sections(player, show_all=request.GET.get("all") == "1")
    return render(request, "stats/entry.html", dict(context, game=game))
