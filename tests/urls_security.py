"""Test-only URLconf: a view that touches the session and CSRF cookie."""

from django.http import HttpResponse
from django.middleware.csrf import get_token
from django.urls import path


def probe(request):
    request.session["probe"] = True
    get_token(request)
    return HttpResponse(f"secure={request.is_secure()}")


urlpatterns = [path("probe/", probe)]
