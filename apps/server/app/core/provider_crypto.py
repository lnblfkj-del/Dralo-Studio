"""Provider API Key 加密与掩码。"""

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.errors import ProviderConfigurationError


def _cipher() -> Fernet:
    if not settings.provider_encryption_key:
        raise ProviderConfigurationError()
    try:
        return Fernet(settings.provider_encryption_key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ProviderConfigurationError("模型渠道加密密钥格式无效") from exc


def encrypt_api_key(api_key: str) -> str:
    return _cipher().encrypt(api_key.encode("utf-8")).decode("ascii")


def decrypt_api_key(ciphertext: str) -> str:
    try:
        return _cipher().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise ProviderConfigurationError("模型渠道密钥无法解密，请重新保存") from exc


def api_key_hint(api_key: str) -> str:
    if api_key.lstrip().startswith("{"):
        return "pair"
    return api_key[-4:] if len(api_key) >= 4 else api_key


def masked_api_key(hint: str) -> str:
    return f"****{hint}"
