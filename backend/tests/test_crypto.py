import pytest
from cryptography.fernet import Fernet

import config
import crypto
from crypto import TokenCipher, TokenDecryptionError, get_cipher


@pytest.fixture
def cipher():
    return TokenCipher(Fernet.generate_key().decode())


def test_a_value_reads_back_the_same(cipher):
    assert cipher.decrypt(cipher.encrypt("1000.abc123.def456")) == "1000.abc123.def456"


def test_the_stored_form_does_not_contain_the_value(cipher):
    stored = cipher.encrypt("1000.abc123.def456")
    assert "abc123" not in stored
    assert "1000." not in stored


def test_the_same_value_is_stored_differently_each_time(cipher):
    assert cipher.encrypt("token") != cipher.encrypt("token")


def test_empty_and_unusual_values_survive(cipher):
    for value in ["", " ", "tökén-✓", "a" * 5000]:
        assert cipher.decrypt(cipher.encrypt(value)) == value


def test_another_key_cannot_read_it(cipher):
    stored = cipher.encrypt("secret")
    other = TokenCipher(Fernet.generate_key().decode())
    with pytest.raises(TokenDecryptionError):
        other.decrypt(stored)


def test_damaged_or_plain_text_cannot_be_read(cipher):
    stored = cipher.encrypt("secret")
    with pytest.raises(TokenDecryptionError):
        cipher.decrypt(stored[:-4] + "AAAA")
    with pytest.raises(TokenDecryptionError):
        cipher.decrypt("not encrypted at all")


def test_the_error_does_not_reveal_the_value(cipher):
    with pytest.raises(TokenDecryptionError) as caught:
        cipher.decrypt("1000.my-real-token")
    assert "my-real-token" not in str(caught.value)


def test_the_app_cipher_uses_the_configured_key(monkeypatch):
    key = Fernet.generate_key().decode()
    settings = config.Settings(
        _env_file=None,
        anthropic_api_key="k",
        anthropic_model="m",
        frontend_origin="http://localhost:3000",
        access_key="a-long-enough-test-key",
        database_url="sqlite://",
        token_encryption_key=key,
    )
    monkeypatch.setattr(crypto, "get_settings", lambda: settings)
    assert get_cipher().decrypt(TokenCipher(key).encrypt("x")) == "x"


def test_the_app_cipher_needs_a_key(monkeypatch):
    settings = config.Settings(
        _env_file=None,
        anthropic_api_key="k",
        anthropic_model="m",
        frontend_origin="http://localhost:3000",
        access_key="a-long-enough-test-key",
        database_url="sqlite://",
        token_encryption_key="",
    )
    monkeypatch.setattr(crypto, "get_settings", lambda: settings)
    with pytest.raises(RuntimeError, match="TOKEN_ENCRYPTION_KEY"):
        get_cipher()