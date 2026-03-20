"""
┌──────────────────────────────────────────────────────────────────────────────┐
│ @modified: remove encryption (dev mode)                                      │
│ @note: API keys are now stored as plain text                                 │
└──────────────────────────────────────────────────────────────────────────────┘
"""

import logging

logger = logging.getLogger(__name__)


def encrypt_api_key(api_key: str) -> str:
    """
    Dev mode: no encryption, return original value
    """
    if not api_key:
        return ""

    # 可选：打个 warning 提醒当前是明文模式
    logger.warning("⚠️ API key encryption is DISABLED (dev mode)")

    return api_key


def decrypt_api_key(encrypted_key: str) -> str:
    """
    Dev mode: no decryption, return original value
    """
    if not encrypted_key:
        return ""

    return encrypted_key