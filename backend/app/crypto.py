"""AES-256-GCM encryption for secrets stored in the database (API keys, DB passwords).

Each value is bound to a context string (e.g. "provider_key:anthropic") so an
encrypted value copied into a different row or column fails to decrypt.
"""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_settings

_NONCE_BYTES = 12


def encrypt(plaintext: str, context: str) -> str:
    aes = AESGCM(get_settings().master_key_bytes)
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = aes.encrypt(nonce, plaintext.encode(), context.encode())
    return base64.b64encode(nonce + ciphertext).decode()


def decrypt(token: str, context: str) -> str:
    aes = AESGCM(get_settings().master_key_bytes)
    raw = base64.b64decode(token)
    nonce, ciphertext = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
    return aes.decrypt(nonce, ciphertext, context.encode()).decode()
