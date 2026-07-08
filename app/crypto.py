"""Fernet encryption for values that must be stored decryptable at rest: the
SimpleFIN bank-sync Access URL (see IMPLEMENTATION_HISTORY.md's Security section) and, since
2026-07-29, TOTP secrets for authenticator-app login. Each secret domain uses its
own env var/key so a leak of one doesn't compromise the other.

Requires BANK_SYNC_ENCRYPTION_KEY / TOTP_ENCRYPTION_KEY in the environment,
generated once via:
    .venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Keys are read lazily inside the encrypt/decrypt functions rather than at import
time or in app/config.py, since nothing calls this module until the relevant
feature is actually used — failing app startup over an unset key would break
unrelated functionality. Losing a key makes every value encrypted under it
unrecoverable; back it up outside the VPS the same way any other credential would be.
"""
import os

from cryptography.fernet import Fernet, InvalidToken


def _get_fernet(env_var: str) -> Fernet:
    key = os.environ.get(env_var)
    if not key:
        raise RuntimeError(
            f"{env_var} is not set. Generate one with Fernet.generate_key() and "
            "set it in the environment before encrypting or decrypting this data."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str) -> bytes:
    return _get_fernet("BANK_SYNC_ENCRYPTION_KEY").encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    try:
        return _get_fernet("BANK_SYNC_ENCRYPTION_KEY").decrypt(ciphertext).decode()
    except InvalidToken:
        raise ValueError("Could not decrypt value — wrong key or corrupted data.")


def encrypt_totp_secret(plaintext: str) -> bytes:
    return _get_fernet("TOTP_ENCRYPTION_KEY").encrypt(plaintext.encode())


def decrypt_totp_secret(ciphertext: bytes) -> str:
    try:
        return _get_fernet("TOTP_ENCRYPTION_KEY").decrypt(ciphertext).decode()
    except InvalidToken:
        raise ValueError("Could not decrypt value — wrong key or corrupted data.")
