"""Player profile, comparison, and team season pages (U7: R18-R22).

Read-only pages for every league member. Totals come from stats.aggregate on
each request; nothing is stored (R18).
"""

from django.shortcuts import render

from leagues import policy
from leagues.access import get_in_league, league_view
from leagues.models import Player, Season

from . import aggregate
from .models import StatLine


def _season_label(season):
    return f"{season.team.name} {season.name}"


def _group_by_season(lines):
    """[(season, [lines...]), ...] in the order the lines come in."""
    seasons = {}
    for line in lines:
        seasons.setdefault(line.game.season, []).append(line)
    return list(seasons.items())


def _summary_rows(label, summary, columns, per_game_label):
    return [
        {"label": label, "gp": summary.games_played, "cells": [aggregate.total_text(summary, c) for c in columns]},
        {"label": per_game_label, "gp": "", "cells": [aggregate.average_text(summary, c) for c in columns]},
    ]


@league_view
def player_profile(request, league_id, player_id):
    player = get_in_league(Player, request.league, pk=player_id)
    lines = list(
        StatLine.objects.filter(player=player)
        .select_related("game__season__team")
        .order_by("game__season_id", "game__order", "game_id")
    )
    career = aggregate.summarize(lines)
    by_season = [(season, season_lines, aggregate.summarize(season_lines)) for season, season_lines in _group_by_season(lines)]

    season_tables = []
    for category, label, columns in aggregate.category_columns(career.categories):
        rows = []
        for season, _season_lines, summary in by_season:
            rows += _summary_rows(_season_label(season), summary, columns, "Per game")
        rows += _summary_rows("Career", career, columns, "Career per game")
        season_tables.append({"category": category, "label": label, "columns": columns, "rows": rows})

    log_columns = [
        column for _c, _l, columns in aggregate.category_columns(career.categories, derived=False) for column in columns
    ]
    game_log = [
        {
            "season": _season_label(season),
            "rows": [
                {
                    "line": line,
                    "game": line.game,
                    "cells": [aggregate.format_total(line.values.get(c.key)) for c in log_columns],
                }
                for line in season_lines
            ],
        }
        for season, season_lines, _summary in by_season
    ]
    return render(
        request,
        "stats/profile.html",
        {
            "player": player,
            "career": career,
            "season_tables": season_tables,
            "log_columns": log_columns,
            "game_log": game_log,
            "can_edit_bio": policy.can_edit_player_bio(request.user, player),
        },
    )


@league_view
def compare(request, league_id):
    """Two or more players side by side, for one season or career (R21)."""
    league = request.league
    seasons = list(Season.objects.filter(team__league=league).select_related("team").order_by("id"))
    picked_ids = list(dict.fromkeys(int(pk) for pk in request.GET.getlist("players") if pk.isdigit()))
    picked = [get_in_league(Player, league, pk=pk) for pk in picked_ids]

    scope = request.GET.get("scope", "career")
    season = get_in_league(Season, league, pk=int(scope)) if scope.isdigit() else None

    context = {
        "players": league.players.all(),
        "picked_ids": picked_ids,
        "seasons": seasons,
        "season": season,
        "need_more": len(picked) == 1,
    }
    if len(picked) >= 2:
        lines = StatLine.objects.filter(player__in=picked).select_related("game")
        if season:
            lines = lines.filter(game__season=season)
        lines = list(lines)
        summaries = [aggregate.summarize(line for line in lines if line.player_id == p.pk) for p in picked]
        categories = {category for summary in summaries for category in summary.categories}
        groups = aggregate.category_columns(categories)

        def cell(summary, column):
            return {"total": aggregate.total_text(summary, column), "average": aggregate.average_text(summary, column)}

        context["compared"] = picked
        context["games_played"] = [summary.games_played for summary in summaries]
        context["table"] = [
            {"label": label, "rows": [{"label": c.label, "cells": [cell(s, c) for s in summaries]} for c in columns]}
            for _category, label, columns in groups
        ]
        context["cards"] = [
            {
                "player": player,
                "games_played": summary.games_played,
                "groups": [
                    {"label": label, "rows": [{"label": c.label, **cell(summary, c)} for c in columns]}
                    for _category, label, columns in groups
                ],
            }
            for player, summary in zip(picked, summaries)
        ]
    return render(request, "stats/compare.html", context)


@league_view
def team_season(request, league_id, season_id):
    """Schedule with results, W-L-T with playoffs separate, and stat leaders (R22)."""
    season = get_in_league(Season, request.league, pk=season_id)
    games = list(season.games.all())
    lines = list(StatLine.objects.filter(game__season=season).select_related("player").order_by("player__name"))
    by_player = {}
    for line in lines:
        by_player.setdefault(line.player, []).append(line)
    summaries = [(player, aggregate.summarize(player_lines)) for player, player_lines in by_player.items()]
    return render(
        request,
        "stats/team_season.html",
        {
            "season": season,
            "games": games,
            "record": aggregate.season_record(games),
            "leaders": aggregate.leaders(summaries),
        },
    )
