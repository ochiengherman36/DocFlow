import datetime

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from crypto import TokenCipher, TokenDecryptionError
from models import ZohoConnection
from zoho_store import delete_tokens, get_tokens, save_tokens

ACCESS = "1000.access-token-abc123.xyz"
REFRESH = "1000.refresh-token-def456.xyz"
EXPIRES = datetime.datetime(2026, 11, 1, 12, 0, tzinfo=datetime.UTC)


@pytest.fixture
def cipher():
    return TokenCipher(Fernet.generate_key().decode())


def save(session, cipher, **overrides):
    values = dict(
        access_token=ACCESS,
        refresh_token=REFRESH,
        expires_at=EXPIRES,
        scope="ZohoBooks.bills.CREATE",
        api_domain="https://www.zohoapis.com",
        connected_by="Herman",
    )
    save_tokens(session, cipher, **{**values, **overrides})
    session.commit()


def test_nothing_is_stored_at_first(database, cipher):
    with database() as session:
        assert get_tokens(session, cipher) is None


def test_saved_tokens_read_back_through_the_app(database, cipher):
    with database() as session:
        save(session, cipher)
    with database() as session:
        tokens = get_tokens(session, cipher)
    assert tokens.access_token == ACCESS
    assert tokens.refresh_token == REFRESH
    assert tokens.expires_at == EXPIRES
    assert tokens.scope == "ZohoBooks.bills.CREATE"
    assert tokens.api_domain == "https://www.zohoapis.com"
    assert tokens.connected_by == "Herman"
    assert tokens.connected_at.tzinfo is not None


def test_the_raw_row_holds_no_readable_token(database, cipher):
    with database() as session:
        save(session, cipher)
        row = session.execute(text("SELECT access_token_enc, refresh_token_enc FROM zoho_connection")).one()

    for stored in row:
        assert "access-token" not in stored
        assert "refresh-token" not in stored
        assert "1000." not in stored
    assert cipher.decrypt(row.access_token_enc) == ACCESS
    assert cipher.decrypt(row.refresh_token_enc) == REFRESH


def test_no_column_holds_a_token_in_plain_text(database, cipher):
    with database() as session:
        save(session, cipher)
        everything = session.execute(text("SELECT * FROM zoho_connection")).one()
    assert not any(isinstance(v, str) and ("abc123" in v or "def456" in v) for v in everything)


def test_a_different_key_cannot_read_the_stored_tokens(database, cipher):
    with database() as session:
        save(session, cipher)
    other = TokenCipher(Fernet.generate_key().decode())
    with database() as session:
        with pytest.raises(TokenDecryptionError):
            get_tokens(session, other)


def test_connecting_again_replaces_the_tokens(database, cipher):
    with database() as session:
        save(session, cipher)
        save(session, cipher, access_token="1000.new-access", refresh_token="1000.new-refresh", connected_by="Wanjiru")
    with database() as session:
        tokens = get_tokens(session, cipher)
        count = session.execute(text("SELECT COUNT(*) FROM zoho_connection")).scalar()
    assert count == 1
    assert (tokens.access_token, tokens.refresh_token, tokens.connected_by) == ("1000.new-access", "1000.new-refresh", "Wanjiru")


def test_only_one_connection_can_exist(database, cipher):
    with database() as session:
        save(session, cipher)
        session.add(
            ZohoConnection(
                id=2,
                access_token_enc="x",
                refresh_token_enc="y",
                expires_at=EXPIRES,
                connected_by="Someone",
                connected_at=EXPIRES,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_disconnecting_removes_the_row(database, cipher):
    with database() as session:
        save(session, cipher)
    with database() as session:
        assert delete_tokens(session) is True
        session.commit()
    with database() as session:
        assert get_tokens(session, cipher) is None
        assert session.execute(text("SELECT COUNT(*) FROM zoho_connection")).scalar() == 0


def test_disconnecting_when_not_connected_is_harmless(database):
    with database() as session:
        assert delete_tokens(session) is False


def test_an_expiry_without_a_timezone_is_treated_as_utc(database, cipher):
    with database() as session:
        save(session, cipher, expires_at=datetime.datetime(2026, 11, 1, 12, 0))
    with database() as session:
        assert get_tokens(session, cipher).expires_at == EXPIRES


def test_an_expiry_in_another_timezone_is_stored_as_the_same_moment(database, cipher):
    nairobi = datetime.timezone(datetime.timedelta(hours=3))
    with database() as session:
        save(session, cipher, expires_at=datetime.datetime(2026, 11, 1, 15, 0, tzinfo=nairobi))
    with database() as session:
        assert get_tokens(session, cipher).expires_at == EXPIRES