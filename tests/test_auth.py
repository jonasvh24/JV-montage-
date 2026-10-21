import base64

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from nullmontage.auth import PasswordGate


def _client(password="s3cret"):
    app = FastAPI()

    @app.get("/")
    def index():
        return {"page": True}

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/events")
    def events():
        return StreamingResponse(iter([b"data: 1\n\n", b"data: 2\n\n"]), media_type="text/event-stream")

    app.add_middleware(PasswordGate, password=password)
    return TestClient(app)


def _basic(user, pw):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def test_no_credentials_gets_challenge():
    r = _client().get("/")
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Basic ")


def test_wrong_password_rejected():
    assert _client().get("/", headers=_basic("anyone", "nope")).status_code == 401


def test_right_password_any_username():
    c = _client()
    assert c.get("/", headers=_basic("jvh", "s3cret")).json() == {"page": True}
    assert c.get("/", headers=_basic("", "s3cret")).status_code == 200


def test_malformed_header_rejected():
    c = _client()
    assert c.get("/", headers={"Authorization": "Basic !!!not-base64"}).status_code == 401
    assert c.get("/", headers={"Authorization": "Bearer s3cret"}).status_code == 401
    assert c.get("/", headers={"Authorization": "Basic " + base64.b64encode(b"s3cret").decode()}).status_code == 401


def test_health_is_open():
    assert _client().get("/healthz").json() == {"ok": True}


def test_streaming_passes_through():
    r = _client().get("/events", headers=_basic("u", "s3cret"))
    assert r.status_code == 200
    assert r.text == "data: 1\n\ndata: 2\n\n"
