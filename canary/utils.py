"""
Canary Test Utilities

Standalone utilities for canary tests that don't depend on the main app.
This ensures the canary module can run independently.
"""

import os
import logging
from datetime import datetime, timezone
from typing import Optional
import pyotp
from cryptography.fernet import Fernet


# TOTP validity period - must match app/shared/utils/totp.py
VALIDITY_PERIOD = 15 * 60  # 15 minutes in seconds


def get_canary_logger(name: str) -> logging.Logger:
    """Get a simple logger for canary tests."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def decrypt_string(encrypted_value: str) -> str:
    """
    Decrypt a Fernet-encrypted string.

    Standalone implementation that reads FERNET_ENCRYPTION_KEY from environment.
    This avoids dependency on app.shared.config which requires many other settings.

    NOTE: The encryption in app/shared/utils/encryption.py does an extra base64 encode
    after Fernet encryption, so we need to base64 decode first before Fernet decryption.
    """
    import base64

    encryption_key = os.getenv("FERNET_ENCRYPTION_KEY")
    if not encryption_key:
        raise ValueError("FERNET_ENCRYPTION_KEY not found in environment variables")

    fernet = Fernet(encryption_key.encode() if isinstance(encryption_key, str) else encryption_key)

    # First base64 decode (to undo the extra encoding done during encryption)
    encrypted_bytes = base64.urlsafe_b64decode(encrypted_value.encode())

    # Then Fernet decrypt
    decrypted = fernet.decrypt(encrypted_bytes)
    return decrypted.decode()


def _get_start_time() -> int:
    """Get the start time for TOTP calculation.

    The app uses 15-minute windows, rounded down to the start of the window.
    This must match app/shared/utils/totp.py._get_start_time()
    """
    current_time = int(datetime.now(timezone.utc).timestamp())
    return int(current_time - (current_time % VALIDITY_PERIOD))


def generate_totp_token(secret: str) -> str:
    """Generate a TOTP token from a secret.

    IMPORTANT: Uses 15-minute windows to match app/shared/utils/totp.py
    """
    totp = pyotp.TOTP(secret)
    return totp.at(_get_start_time())


def get_database_url() -> str:
    """Get database connection URL from settings or environment."""
    # Try to load from app settings first
    try:
        # from app.shared.config import get_settings
        settings = get_settings()
        if settings.DATABASE_URL:
            return settings.DATABASE_URL
    except Exception:
        pass

    # Fall back to environment variables
    db_user = os.getenv("POSTGRES_USER", "maxeo")
    db_pass = os.getenv("POSTGRES_PASSWORD", "")
    db_host = os.getenv("POSTGRES_HOST", "localhost")
    db_port = os.getenv("POSTGRES_PORT", "5432")
    db_name = os.getenv("POSTGRES_DB", "maxeo")

    return f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
