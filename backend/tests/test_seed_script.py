import json

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from db import Base, make_engine
from models import Supplier
from scripts import seed_suppliers as script


@pytest.fixture
def factory(monkeypatch):
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(script, "get_sessionmaker", lambda: factory)
    return factory


def write(tmp_path, names):
    path = tmp_path / "suppliers.json"
    path.write_text(json.dumps(names), encoding="utf-8")
    return str(path)


def stored(factory) -> list[str]:
    with Session(factory.kw["bind"]) as session:
        return sorted(session.scalars(select(Supplier.name)))


def test_the_script_fills_the_table_from_the_file(factory, tmp_path, capsys):
    path = write(tmp_path, ["Kijani Fuel", "Twiga Auto Spares"])
    assert script.main(["seed", path]) == 0
    assert stored(factory) == ["Kijani Fuel", "Twiga Auto Spares"]
    out = capsys.readouterr().out
    assert "Read 2 names" in out and "Added 2; 0 were already stored" in out and "now holds 2" in out


def test_running_it_again_adds_nothing(factory, tmp_path, capsys):
    path = write(tmp_path, ["Kijani Fuel", "Twiga Auto Spares"])
    script.main(["seed", path])
    capsys.readouterr()
    assert script.main(["seed", path]) == 0
    assert stored(factory) == ["Kijani Fuel", "Twiga Auto Spares"]
    assert "Added 0; 2 were already stored" in capsys.readouterr().out


def test_names_already_stored_under_another_spelling_are_not_duplicated(factory, tmp_path):
    script.main(["seed", write(tmp_path, ["Twiga Auto Spares Ltd"])])
    script.main(["seed", write(tmp_path, ["twiga auto spares limited"])])
    assert stored(factory) == ["Twiga Auto Spares Ltd"]


def test_the_shipped_file_is_used_when_no_path_is_given(factory, capsys):
    assert script.main(["seed"]) == 0
    assert stored(factory) == ["Kijani Fuel Distributors Ltd", "Mwangaza Office Supplies", "Twiga Auto Spares"]


def test_a_missing_file_is_reported_without_touching_the_table(factory, tmp_path, capsys):
    assert script.main(["seed", str(tmp_path / "nope.json")]) == 1
    assert "FAILED" in capsys.readouterr().out
    assert stored(factory) == []


def test_a_broken_file_is_reported(factory, tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text("not json", encoding="utf-8")
    assert script.main(["seed", str(path)]) == 1
    assert "not valid JSON" in capsys.readouterr().out


def test_an_unreachable_database_is_reported_clearly(monkeypatch, tmp_path, capsys):
    def broken():
        raise OperationalError("connect", {}, Exception("password authentication failed for user 'x'"))

    monkeypatch.setattr(script, "get_sessionmaker", broken)
    assert script.main(["seed", write(tmp_path, ["Kijani Fuel"])]) == 1
    out = capsys.readouterr().out
    assert "refused the login" in out
    assert "scripts.check_db" in out


def test_missing_tables_on_postgres_are_explained(monkeypatch, tmp_path, capsys):
    def broken():
        raise ProgrammingError("INSERT INTO suppliers", {}, Exception('relation "suppliers" does not exist'))

    monkeypatch.setattr(script, "get_sessionmaker", broken)
    assert script.main(["seed", write(tmp_path, ["Kijani Fuel"])]) == 1
    out = capsys.readouterr().out
    assert "The tables are missing" in out
    assert "alembic upgrade head" in out
    assert "database name was not found" not in out


def test_missing_tables_on_sqlite_are_explained_when_the_table_does_not_exist(tmp_path, capsys, monkeypatch):
    engine = make_engine("sqlite://")
    monkeypatch.setattr(script, "get_sessionmaker", lambda: sessionmaker(bind=engine, expire_on_commit=False))
    assert script.main(["seed", write(tmp_path, ["Kijani Fuel"])]) == 1
    assert "The tables are missing" in capsys.readouterr().out