"""
Centralized encryption/decryption module with audit logging.

Security model:
  - Secret keys are encrypted with Fernet (AES-128-CBC + HMAC-SHA256)
  - Fernet key is derived from Django SECRET_KEY (env variable only, never in code)
  - Every decryption is logged with a full call stack for audit trail
  - Encrypted keys in the database are useless without the SECRET_KEY env variable
"""

import base64
import logging
import traceback
from datetime import datetime

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger("payments.security")


def get_fernet_key() -> Fernet:
    """
    Derive a Fernet cipher from Django SECRET_KEY.
    Uses first 32 characters, padded to 32 bytes, then base64-encoded.
    NEVER log this key or expose it anywhere.
    """
    raw = settings.SECRET_KEY[:32].encode("utf-8")
    padded = raw.ljust(32, b"\x00")          # pad to exactly 32 bytes
    key = base64.urlsafe_b64encode(padded)   # 44-char base64 → valid Fernet key
    return Fernet(key)


def encrypt_secret(raw_secret: str) -> str:
    """
    Encrypt a Stellar secret key before storing in the database.

    Args:
        raw_secret: The plaintext Stellar secret (e.g. S…)

    Returns:
        Fernet-encrypted token as a UTF-8 string suitable for TextField storage.
    """
    logger.info("Encryption performed at %s", datetime.utcnow().isoformat())
    f = get_fernet_key()
    return f.encrypt(raw_secret.encode("utf-8")).decode("utf-8")


def decrypt_secret(encrypted_secret: str) -> str:
    """
    Decrypt a stored Stellar secret key.

    IMPORTANT: Call this ONLY immediately before signing a transaction.
    Every call is logged with a full stack trace for audit compliance.

    Args:
        encrypted_secret: The Fernet token stored in the database.

    Returns:
        The plaintext Stellar secret key.

    Raises:
        InvalidToken: If the token is corrupted or the wrong key is used.
        ValueError: If the encrypted_secret is empty/None.
    """
    if not encrypted_secret:
        raise ValueError("Cannot decrypt an empty secret.")

    # Capture the call stack BEFORE doing anything sensitive
    stack = "".join(traceback.format_stack()[:-1])
    logger.warning(
        "SECRET KEY DECRYPTION at %s\nCall stack:\n%s",
        datetime.utcnow().isoformat(),
        stack,
    )

    try:
        f = get_fernet_key()
        return f.decrypt(encrypted_secret.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        logger.error(
            "Decryption FAILED at %s — possible key mismatch or data corruption.",
            datetime.utcnow().isoformat(),
        )
        raise InvalidToken("Failed to decrypt secret key. Key mismatch or corruption.") from exc
