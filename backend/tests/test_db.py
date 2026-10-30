import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

import db
from db import check_connection, get_db, make_engine


def test_sqlite_engine_can_run_a_query():
    engine = make_engine("sqlite://")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar() == 1


def test_postgres_address_builds_an_engine_with_the_psycopg_driver_without_connecting():
    engine = make_engine("postgresql+psycopg://user:pass@db.example.com/appdb?sslmode=require")
    assert engine.dialect.name == "postgresql"
    assert engine.dialect.driver == "psycopg"
    assert engine.url.host == "db.example.com"
    assert engine.url.database == "appdb"
    assert engine.url.query == {"sslmode": "require"}


def test_postgres_engine_password_is_hidden_when_printed():
    engine = make_engine("postgresql+psycopg://user:s3cret-pass@db.example.com/appdb")
    assert "s3cret-pass" not in repr(engine)
    assert "s3cret-pass" not in engine.url.render_as_string(hide_password=True)


def test_check_connection_reports_server_and_timing():
    info = check_connection(make_engine("sqlite://"))
    assert info.server
    assert info.milliseconds >= 0


def test_check_connection_fails_for_an_unreachable_database():
    engine = make_engine("postgresql+psycopg://user:pass@127.0.0.1:1/appdb", connect_timeout=1)
    with pytest.raises(OperationalError):
        check_connection(engine)


def test_the_app_engine_is_built_from_the_configured_address():
    db.get_engine.cache_clear()
    try:
        assert db.get_engine().dialect.name == "sqlite"
    finally:
        db.get_engine.cache_clear()


@pytest.fixture
def client(monkeypatch):
    engine = make_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)"))
    monkeypatch.setattr(db, "get_sessionmaker", lambda: sessionmaker(bind=engine, expire_on_commit=False))

    app = FastAPI()

    @app.post("/note/{body}")
    def add_note(body: str, session: Session = Depends(get_db)):
        session.execute(text("INSERT INTO notes (body) VALUES (:b)"), {"b": body})
        session.commit()
        return {"saved": body}

    @app.post("/note-then-fail/{body}")
    def add_note_then_fail(body: str, session: Session = Depends(get_db)):
        session.execute(text("INSERT INTO notes (body) VALUES (:b)"), {"b": body})
        raise RuntimeError("something went wrong after the insert")

    @app.get("/notes")
    def list_notes(session: Session = Depends(get_db)):
        return [row[0] for row in session.execute(text("SELECT body FROM notes ORDER BY id"))]

    return TestClient(app, raise_server_exceptions=False)


def test_a_committed_change_is_kept(client):
    assert client.post("/note/hello").json() == {"saved": "hello"}
    assert client.get("/notes").json() == ["hello"]


def test_a_change_that_is_not_committed_is_rolled_back(client):
    client.post("/note/kept")
    r = client.post("/note-then-fail/lost")
    assert r.status_code == 500
    assert client.get("/notes").json() == ["kept"]


def test_each_request_gets_its_own_session(client):
    sessions = []
    original = db.get_db

    def spy():
        for session in original():
            sessions.append(session)
            yield session

    client.app.dependency_overrides[get_db] = spy
    client.get("/notes")
    client.get("/notes")
    assert len(sessions) == 2 and sessions[0] is not sessions[1]