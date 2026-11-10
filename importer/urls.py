"""Legacy backup import and league export/import (U8, U9).

Everything under "league/<int:league_id>/" uses @league_view (leagues.access).
"""

from django.urls import include, path

from . import views

app_name = "importer"

league_patterns = [
    path("import/", views.upload, name="upload"),
    path("import/review/", views.review, name="review"),
    path("import/resolve/", views.resolve, name="resolve"),
    path("import/seasons/<int:season>/acknowledge/", views.acknowledge_missing, name="acknowledge_missing"),
    path("import/confirm/", views.confirm, name="confirm"),
    path("import/discard/", views.discard, name="discard"),
    path("export/", views.export, name="export"),
]

urlpatterns = [
    # Not under "league/": importing a league file creates a new league (U9).
    path("leagues/import/", views.native_import, name="native_import"),
    path("league/<int:league_id>/", include(league_patterns)),
]
