import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

import main
from security import AccessKeyMiddleware

KEY = main.settings.access_key.get_secret_value()
ORIGIN = main.settings.frontend_origin

anonymous = TestClient(main.app)
authorised = TestClient(main.app, headers={"X-Access-Key": KEY})

MISSING = "Missing access key. Send it in the X-Access-Key header."
INVALID = "Invalid access key."


def test_health_is_open_without_a_key():
    r = anonymous.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.parametrize("method, path", [("post", "/extract"), ("post", "/validate")])
def test_protected_endpoints_refuse_a_request_with_no_key(method, path):
    r = getattr(anonymous, method)(path)
    assert r.status_code == 401
    assert r.json() == {"detail": MISSING}


@pytest.mark.parametrize("method, path", [("post", "/extract"), ("post", "/validate")])
def test_protected_endpoints_refuse_a_wrong_key(method, path):
    r = getattr(anonymous, method)(path, headers={"X-Access-Key": "not-the-key"})
    assert r.status_code == 401
    assert r.json() == {"detail": INVALID}


def test_an_empty_key_counts_as_missing():
    r = anonymous.post("/validate", headers={"X-Access-Key": ""})
    assert r.status_code == 401
    assert r.json() == {"detail": MISSING}


@pytest.mark.parametrize(
    "wrong",
    [KEY + "x", KEY[:-1], KEY.upper(), "x" * len(KEY), "clé-d'accès-é", " " + KEY + " "],
)
def test_near_misses_and_odd_characters_are_refused_without_a_crash(wrong):
    r = anonymous.post("/validate", headers={"X-Access-Key": wrong.encode("utf-8")})
    assert r.status_code == 401


def test_the_right_key_gets_through():
    r = authorised.post("/validate", json={})
    assert r.status_code == 200
    assert "flags" in r.json()


def test_the_right_key_on_extract_reaches_the_endpoint():
    r = authorised.post("/extract")
    assert r.status_code == 400
    assert "No file uploaded" in r.json()["detail"]


def test_the_key_is_checked_before_the_request_body_is_validated():
    r = anonymous.post("/validate", content="not json", headers={"content-type": "application/json"})
    assert r.status_code == 401


def test_unknown_paths_are_protected_too():
    assert anonymous.get("/anything-else").status_code == 401
    assert authorised.get("/anything-else").status_code == 404


def test_docs_are_switched_off_by_default():
    assert authorised.get("/docs").status_code == 404
    assert authorised.get("/openapi.json").status_code == 404
    assert anonymous.get("/docs").status_code == 401


def test_a_refusal_still_carries_cors_headers_so_the_browser_can_read_it():
    r = anonymous.post("/extract", headers={"Origin": ORIGIN})
    assert r.status_code == 401
    assert r.headers["access-control-allow-origin"] == ORIGIN


def test_the_preflight_for_the_key_header_is_answered_without_a_key():
    r = anonymous.options(
        "/extract",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-access-key,content-type",
        },
    )
    assert r.status_code == 200
    assert "x-access-key" in r.headers["access-control-allow-headers"].lower()


def test_a_preflight_from_another_origin_is_still_refused():
    r = anonymous.options(
        "/extract",
        headers={"Origin": "http://evil.example.com", "Access-Control-Request-Method": "POST"},
    )
    assert r.status_code == 400


def make_app(**kwargs):
    async def ok(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/open", ok), Route("/secret", ok), Route("/docs", ok)])
    app.add_middleware(AccessKeyMiddleware, access_key="a-secret-key-1234", **kwargs)
    return TestClient(app)


def test_custom_public_paths_are_honoured():
    client = make_app(public_paths={"/open", "/docs"})
    assert client.get("/open").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/secret").status_code == 401
    assert client.get("/secret", headers={"X-Access-Key": "a-secret-key-1234"}).status_code == 200


def test_nothing_is_public_unless_listed():
    client = make_app(public_paths=())
    assert client.get("/open").status_code == 401


def test_the_request_body_is_never_read_for_a_refused_request():
    async def downstream(scope, receive, send):
        raise AssertionError("the app must not be reached")

    middleware = AccessKeyMiddleware(downstream, access_key="a-secret-key-1234")
    sent = []

    async def receive():
        raise AssertionError("the body must not be read")

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/extract", "headers": [(b"content-length", b"50000000")]}
    asyncio.run(middleware(scope, receive, send))
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 401

def test_auth_check_refuses_without_a_key_and_accepts_the_right_one():
    assert anonymous.get("/auth/check").status_code == 401
    assert anonymous.get("/auth/check", headers={"X-Access-Key": "wrong"}).json() == {"detail": INVALID}
    r = authorised.get("/auth/check")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}