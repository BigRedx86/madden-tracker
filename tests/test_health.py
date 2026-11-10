"""Health endpoint used by the Docker HEALTHCHECK (KTD9)."""


def test_health_returns_ok_without_signing_in(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.content == b"ok"
    assert response["Content-Type"].startswith("text/plain")
    assert not response.cookies


def test_health_answers_the_container_healthcheck_host(client):
    # The healthcheck calls 127.0.0.1:8000, which is not in ALLOWED_HOSTS
    # and is plain HTTP; it must still get "ok", not 400 or a redirect.
    response = client.get("/health", HTTP_HOST="127.0.0.1:8000")
    assert response.status_code == 200
    assert response.content == b"ok"


def test_other_paths_still_validate_host(client):
    response = client.get("/health/extra", HTTP_HOST="127.0.0.1:8000")
    assert response.status_code == 400
