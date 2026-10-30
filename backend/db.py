import time
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import MetaData, create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from config import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def make_engine(url: str, connect_timeout: int = 10) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(engine, "connect")
        def enforce_foreign_keys(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={
            "connect_timeout": connect_timeout,
            "prepare_threshold": None,
        },
    )


@lru_cache
def get_engine() -> Engine:
    return make_engine(get_settings().database_url.get_secret_value())


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    with get_sessionmaker()() as session:
        yield session


@dataclass(frozen=True)
class ConnectionInfo:
    server: str
    milliseconds: float


def check_connection(engine: Engine) -> ConnectionInfo:
    started = time.perf_counter()
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        version_query = "SELECT sqlite_version()" if engine.dialect.name == "sqlite" else "SELECT version()"
        version = str(connection.execute(text(version_query)).scalar())
    elapsed = (time.perf_counter() - started) * 1000
    return ConnectionInfo(server=version.splitlines()[0], milliseconds=elapsed)