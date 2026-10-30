import io
import types

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import config
from config import BASE_DIR
from db import Base, make_engine
from models import Document

TABLES = {"suppliers", "documents", "extractions", "audit_log"}
INVOICE = {"supplier": "Kijani", "invoice_number": "1"}


def alembic_config(connection=None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


@pytest.fixture
def engine():
    return make_engine("sqlite://")


def upgrade(engine, revision="head"):
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), revision)


def downgrade(engine, revision="base"):
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), revision)


def table_names(engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def test_there_is_a_single_migration():
    script = ScriptDirectory(str(BASE_DIR / "migrations"))
    assert script.get_heads() == ["0001"]


def test_upgrade_creates_every_table(engine):
    upgrade(engine)
    assert table_names(engine) == TABLES | {"alembic_version"}


def test_downgrade_removes_every_table(engine):
    upgrade(engine)
    downgrade(engine)
    assert table_names(engine) == {"alembic_version"}


def test_upgrade_works_again_after_a_downgrade(engine):
    upgrade(engine)
    downgrade(engine)
    upgrade(engine)
    assert table_names(engine) == TABLES | {"alembic_version"}


def test_running_upgrade_twice_changes_nothing(engine):
    upgrade(engine)
    upgrade(engine)
    assert table_names(engine) == TABLES | {"alembic_version"}


def test_the_database_records_which_version_it_is_at(engine):
    upgrade(engine)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0001"
    downgrade(engine)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM alembic_version")).scalar() == 0


def test_the_models_and_the_migration_describe_the_same_database(engine):
    upgrade(engine)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []


def test_the_migrated_database_has_the_partial_unique_index(engine):
    upgrade(engine)
    with engine.connect() as connection:
        sql = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE name = 'uq_documents_approved_invoice'")
        ).scalar()
    assert "UNIQUE" in sql
    assert "supplier_key, invoice_number_key" in sql
    assert "WHERE status = 'approved'" in sql


def make_document(n: int, status: str) -> Document:
    return Document(
        filename=f"{n}.pdf",
        file_hash=f"{n:064x}",
        file_size=1,
        current_data=INVOICE,
        status=status,
        supplier_key="kijani",
        invoice_number_key="kfd-1",
    )


def test_the_migrated_database_enforces_the_approved_invoice_rule(engine):
    upgrade(engine)
    with Session(engine) as session:
        session.add_all([make_document(1, "approved"), make_document(2, "pending"), make_document(3, "rejected")])
        session.commit()
        session.add(make_document(4, "approved"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_the_migrated_database_enforces_unique_files_and_valid_statuses(engine):
    upgrade(engine)
    with Session(engine) as session:
        session.add(make_document(1, "pending"))
        session.commit()
        session.add(make_document(1, "pending"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        session.add(make_document(9, "finished"))
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.fixture
def postgres_sql(monkeypatch):
    fake = types.SimpleNamespace(
        database_url=types.SimpleNamespace(get_secret_value=lambda: "postgresql+psycopg://user:pass@localhost/appdb")
    )
    monkeypatch.setattr(config, "get_settings", lambda: fake)
    cfg = alembic_config()
    cfg.output_buffer = io.StringIO()
    command.upgrade(cfg, "head", sql=True)
    return cfg.output_buffer.getvalue()


def test_postgres_gets_native_types(postgres_sql):
    assert "id UUID NOT NULL" in postgres_sql
    assert "current_data JSONB NOT NULL" in postgres_sql
    assert "data JSONB NOT NULL" in postgres_sql
    assert "details JSONB" in postgres_sql
    assert "created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in postgres_sql
    assert "id BIGSERIAL NOT NULL" in postgres_sql


def test_postgres_gets_the_partial_unique_index(postgres_sql):
    assert (
        "CREATE UNIQUE INDEX uq_documents_approved_invoice ON documents (supplier_key, invoice_number_key) "
        "WHERE status = 'approved'"
    ) in postgres_sql


def test_postgres_gets_the_other_indexes_and_constraints(postgres_sql):
    assert "CREATE UNIQUE INDEX ix_documents_file_hash ON documents (file_hash)" in postgres_sql
    assert "CREATE INDEX ix_documents_invoice_key ON documents (supplier_key, invoice_number_key)" in postgres_sql
    assert "CREATE INDEX ix_audit_log_document_time ON audit_log (document_id, created_at)" in postgres_sql
    assert "CONSTRAINT ck_documents_status_valid CHECK (status IN ('pending', 'approved', 'rejected'))" in postgres_sql
    assert "ON DELETE RESTRICT" in postgres_sql
    assert "ON DELETE CASCADE" in postgres_sql
    assert "ON DELETE SET NULL" in postgres_sql


def test_postgres_sql_records_the_version(postgres_sql):
    assert "INSERT INTO alembic_version (version_num) VALUES ('0001')" in postgres_sql