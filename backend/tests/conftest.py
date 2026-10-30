import os

import pytest
from sqlalchemy.orm import sessionmaker

os.environ["ANTHROPIC_API_KEY"] = "test-key-not-real"
os.environ["ANTHROPIC_MODEL"] = "test-model"
os.environ["FRONTEND_ORIGIN"] = "http://localhost:3000"
os.environ["MAX_UPLOAD_MB"] = "1"
# The sample invoices are dated October 2026. A huge limit keeps "invoice too old"
# from appearing in tests when this suite is run in a later year.
os.environ["MAX_INVOICE_AGE_DAYS"] = "36500"
os.environ["ACCESS_KEY"] = "test-access-key-0123456789"
os.environ["DATABASE_URL"] = "sqlite://"


@pytest.fixture(autouse=True)
def database():
    """A fresh in-memory database for every test, filled with the shipped supplier list.

    The app is pointed at it, and the fixture hands back a session factory so a test can
    read or change rows directly.
    """
    import main
    from db import Base, get_db, make_engine
    from supplier_store import seed_suppliers
    from suppliers import load_known_suppliers

    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        seed_suppliers(session, load_known_suppliers())
        session.commit()

    def override():
        with factory() as session:
            yield session

    main.app.dependency_overrides[get_db] = override
    yield factory
    main.app.dependency_overrides.pop(get_db, None)