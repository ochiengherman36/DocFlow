from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from config import Settings, get_settings

class TokenDecryptionError(Exception):
    pass


class TokenCipher:
    def __init__(self, key: str):
        self._fernet = Fernet(key.encode())

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise TokenDecryptionError(
                "A stored token could not be decrypted. The encryption key may have changed."
            ) from exc


@lru_cache
def _cipher_for(key: str) -> TokenCipher:
    return TokenCipher(key)


def get_cipher(settings: Settings | None = None) -> TokenCipher:
    key = (settings or get_settings()).token_encryption_key.get_secret_value()
    if not key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not set.")
    return _cipher_for(key)