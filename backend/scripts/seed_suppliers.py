import sys
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from db import get_sessionmaker
from models import Supplier
from scripts.check_db import explain
from supplier_store import seed_suppliers
from suppliers import DEFAULT_SUPPLIERS_FILE, SupplierListError, load_known_suppliers


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_SUPPLIERS_FILE
    try:
        names = load_known_suppliers(path)
    except SupplierListError as exc:
        print(f"FAILED: {exc}")
        return 1

    try:
        with get_sessionmaker()() as session:
            added = seed_suppliers(session, names)
            session.commit()
            total = session.scalar(select(func.count()).select_from(Supplier))
    except SQLAlchemyError as exc:
        print(f"FAILED: {explain(exc)}")
        print("To test the connection on its own, run: uv run python -m scripts.check_db")
        return 1

    print(f"Read {len(names)} names from {path.name}.")
    print(f"Added {added}; {len(names) - added} were already stored. The suppliers table now holds {total}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))