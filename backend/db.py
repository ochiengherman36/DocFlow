import time
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str, connect_timeout: int = 10) -> Engine:
    if url.startswith("sqlite"):
        return create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
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