"""Encrypt sensitive channel addresses and link-bearing retry responses at rest."""
import base64
import hashlib
import os
from cryptography.fernet import Fernet


def cipher():
    key = os.getenv('SESSION_SIGNING_KEY', '')
    if len(key) < 32:
        raise RuntimeError('A stable signing key is required for channel encryption')
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(('channel:' + key).encode()).digest()))


def seal(text):
    return 'sealed:' + cipher().encrypt(text.encode()).decode()


def unseal(text):
    return cipher().decrypt(text[7:].encode()).decode() if text.startswith('sealed:') else text
