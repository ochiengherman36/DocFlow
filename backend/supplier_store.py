from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Supplier
from suppliers import normalise_name


def supplier_key(name: str) -> str:
    return normalise_name(name)


def list_supplier_names(session: Session) -> list[str]:
    return list(session.scalars(select(Supplier.name).order_by(Supplier.name)))


def add_supplier(session: Session, name: str) -> tuple[Supplier, bool]:
    name = name.strip()
    if not name:
        raise ValueError("A supplier name cannot be blank.")

    key = supplier_key(name)
    existing = session.scalars(select(Supplier).where(Supplier.name_key == key)).first()
    if existing is not None:
        return existing, False

    supplier = Supplier(name=name, name_key=key)
    session.add(supplier)
    session.flush()
    return supplier, True


def seed_suppliers(session: Session, names: Iterable[str]) -> int:
    return sum(1 for name in names if add_supplier(session, name)[1])