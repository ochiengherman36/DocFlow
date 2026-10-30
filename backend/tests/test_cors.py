from fastapi.testclient import TestClient

import main

client = TestClient(main.app)
ALLOWED = "http://localhost:3000"


def test_allowed_origin_gets_cors_header():
    r = client.get("/health", headers={"Origin": ALLOWED})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ALLOWED


def test_other_origin_gets_no_cors_header():
    r = client.get("/health", headers={"Origin": "http://evil.example.com"})
    assert "access-control-allow-origin" not in r.headers


def test_preflight_for_extract_from_allowed_origin():
    r = client.options(
        "/extract",
        headers={
            "Origin": ALLOWED,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ALLOWED
    assert "POST" in r.headers["access-control-allow-methods"]


def test_preflight_from_other_origin_is_refused():
    r = client.options(
        "/extract",
        headers={"Origin": "http://evil.example.com", "Access-Control-Request-Method": "POST"},
    )
    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers