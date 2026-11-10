"""Player profile, comparison, and team season pages (U7).

Everything under "league/<int:league_id>/" uses @league_view (leagues.access).
"""

from django.urls import include, path

from . import views

app_name = "stats"

league_patterns = [
    path("players/<int:player_id>/", views.player_profile, name="profile"),
    path("compare/", views.compare, name="compare"),
    path("seasons/<int:season_id>/stats/", views.team_season, name="team_season"),
]

urlpatterns = [
    path("league/<int:league_id>/", include(league_patterns)),
]
