import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

_secret = os.getenv("APP_SECRET", "change-me-in-production")
_key = base64.urlsafe_b64encode(hashlib.sha256(_secret.encode()).digest())
_fernet = Fernet(_key)


class SecretError(RuntimeError):
    pass


def using_default_secret() -> bool:
    return _secret in {"change-me-in-production", "please-change-this-secret", "replace-with-a-long-random-secret"}


def encrypt_secret(value: str | None) -> str | None:
    return _fernet.encrypt(value.encode()).decode() if value else None


def decrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet.decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise SecretError(
            "Stored repository passphrase cannot be decrypted. APP_SECRET may have changed since the repository was added."
        ) from exc
