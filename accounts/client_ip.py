"""The real client IP behind exactly one trusted proxy hop (R30, KTD5)."""


def client_ip(request):
    """SWAG appends the address it saw to X-Forwarded-For, so the rightmost entry is
    the real client. Entries to its left came from the client and can be forged.
    Without the header (a direct connection), use the socket address.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    entries = [entry.strip() for entry in forwarded.split(",") if entry.strip()]
    if entries:
        return entries[-1]
    return request.META.get("REMOTE_ADDR")
