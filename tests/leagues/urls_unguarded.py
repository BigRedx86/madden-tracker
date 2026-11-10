"""Test-only URLconf with deliberately unguarded league routes."""

from django.http import HttpResponse
from django.urls import include, path, re_path

from leagues.access import league_view


@league_view
def guarded(request, league_id):
    return HttpResponse("ok")


def unguarded(request, **kwargs):
    return HttpResponse("leaky")


def public(request):
    return HttpResponse("public")


nested = [
    path("", guarded),
    path("roster/", unguarded),
]

urlpatterns = [
    path("about/", public),
    path("league/<int:league_id>/", include(nested)),
    re_path(r"^other/(?P<league_id>\d+)/$", unguarded),
    path("league/new/", unguarded),
]
