from django.http import HttpResponse


def health(request):
    """Liveness probe for the Docker HEALTHCHECK. Reads no user or league data (KTD9)."""
    return HttpResponse("ok", content_type="text/plain")
