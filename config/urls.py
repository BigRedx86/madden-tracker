"""Root URLconf.

django.contrib.admin is deliberately not routed (KTD10). /health is served by
ops.middleware.HealthCheckMiddleware, ahead of host validation.

League-owned pages go under "league/<int:league_id>/" and must use
leagues.access.league_view; tests/leagues/test_policy.py fails otherwise.
"""

from django.urls import include, path

urlpatterns = [
    path("", include("accounts.urls")),
    path("", include("leagues.urls")),
    path("", include("stats.urls")),
    path("", include("importer.urls")),
]
