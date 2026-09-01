import base64
import hashlib
import os
from cryptography.fernet import Fernet

_secret = os.getenv("APP_SECRET", "change-me-in-production")
_key = base64.urlsafe_b64encode(hashlib.sha256(_secret.encode()).digest())
_fernet = Fernet(_key)

def encrypt_secret(value: str | None) -> str | None:
    return _fernet.encrypt(value.encode()).decode() if value else None

def decrypt_secret(value: str | None) -> str | None:
    return _fernet.decrypt(value.encode()).decode() if value else None
