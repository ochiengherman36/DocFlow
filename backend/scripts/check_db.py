import sys

from sqlalchemy.exc import OperationalError

from db import check_connection, get_engine

HINTS = [
    ("relation", "The tables are missing. Run: uv run alembic upgrade head"),
    ("no such table", "The tables are missing. Run: uv run alembic upgrade head"),
    ("password authentication failed", "The database refused the login. Check the user and password in DATABASE_URL."),
    ("authentication failed", "The database refused the login. Check the user and password in DATABASE_URL."),
    ("getaddrinfo failed", "The database host name could not be found. Check the host in DATABASE_URL."),
    ("could not translate host name", "The database host name could not be found. Check the host in DATABASE_URL."),
    ("name or service not known", "The database host name could not be found. Check the host in DATABASE_URL."),
    ("does not exist", "The database name was not found. Check the name at the end of DATABASE_URL."),
    ("ssl", "The secure connection failed. Neon needs ?sslmode=require at the end of DATABASE_URL."),
    ("timeout", "The database did not answer in time. If it was asleep, run this again."),
    ("refused", "Nothing is listening at that address. Check the host and port in DATABASE_URL."),
]


def explain(error: Exception) -> str:
    detail = str(getattr(error, "orig", error)).strip().splitlines()[0]
    for needle, hint in HINTS:
        if needle in detail.lower():
            return f"{hint}\n  Detail: {detail}"
    return f"Could not connect.\n  Detail: {detail}"


def main() -> int:
    engine = get_engine()
    target = engine.url.render_as_string(hide_password=True)
    print(f"Connecting to {target}")
    try:
        info = check_connection(engine)
    except OperationalError as exc:
        print(f"FAILED: {explain(exc)}")
        return 1
    print(f"OK: SELECT 1 worked in {info.milliseconds:.0f} ms")
    print(f"Server: {info.server}")
    return 0


if __name__ == "__main__":
    sys.exit(main())