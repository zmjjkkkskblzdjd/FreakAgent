from __future__ import annotations

import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class CryptoConfigError(RuntimeError):
    pass


class SecretDecryptError(RuntimeError):
    pass


def _raw_keys() -> list[str]:
    current = (os.getenv("AF_SECRET_KEY") or "").strip()
    if not current:
        raise CryptoConfigError("AF_SECRET_KEY 未配置；请在 .env 中写入 Fernet key")
    old = [k.strip() for k in (os.getenv("AF_SECRET_KEY_OLD") or "").split(",") if k.strip()]
    return [current, *old]


def current_key_version() -> int:
    return max(1, int(os.getenv("AF_SECRET_KEY_VERSION", "1")))


def _multi_fernet() -> MultiFernet:
    try:
        return MultiFernet([Fernet(k.encode()) for k in _raw_keys()])
    except (ValueError, TypeError) as e:  
        raise CryptoConfigError(f"AF_SECRET_KEY 非法: {e}") from e


def encrypt_secret(plain: str) -> tuple[bytes, int]:
    if plain is None:
        return b"", current_key_version()
    token = _multi_fernet().encrypt(plain.encode("utf-8"))
    return token, current_key_version()


def decrypt_secret(cipher: bytes | None, version: int | None = None) -> str:
    if not cipher:
        return ""
    try:
        return _multi_fernet().decrypt(bytes(cipher)).decode("utf-8")
    except (InvalidToken, ValueError) as e:
        raise SecretDecryptError("密文无法解密（密钥不匹配或被篡改）") from e


def rotate_secret(cipher: bytes | None) -> tuple[bytes, int]:
    return encrypt_secret(decrypt_secret(cipher))


def mask_secret(plain: str | None) -> str:
    if not plain:
        return ""
    text = str(plain)
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:3]}…{text[-4:]}"


def key_fingerprint(plain: str | None) -> str:
    if not plain:
        return ""
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()[:16]


def generate_fernet_key() -> str:
    return Fernet.generate_key().decode()
