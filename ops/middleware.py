from .views import health


class HealthCheckMiddleware:
    """Answer exactly /health before any other middleware runs.

    The container healthcheck calls http://127.0.0.1:8000/health, whose Host is
    not in ALLOWED_HOSTS and which is not HTTPS. Serving it here, ahead of
    host validation and the HTTPS redirect, keeps the probe working without
    loosening either for real pages.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/health":
            return health(request)
        return self.get_response(request)
