import pytest
from cryptography.fernet import Fernet

from app import crypto


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("BANK_SYNC_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", Fernet.generate_key().decode())


def test_round_trip():
    ciphertext = crypto.encrypt("https://key:secret@bridge.simplefin.org/accounts")
    assert crypto.decrypt(ciphertext) == "https://key:secret@bridge.simplefin.org/accounts"


def test_ciphertext_is_not_plaintext():
    ciphertext = crypto.encrypt("super-secret-url")
    assert b"super-secret-url" not in ciphertext


def test_decrypt_with_wrong_key_fails(monkeypatch):
    ciphertext = crypto.encrypt("some-url")
    monkeypatch.setenv("BANK_SYNC_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(ValueError):
        crypto.decrypt(ciphertext)


def test_encrypt_without_key_raises(monkeypatch):
    monkeypatch.delenv("BANK_SYNC_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError):
        crypto.encrypt("some-url")


def test_decrypt_without_key_raises(monkeypatch):
    ciphertext = crypto.encrypt("some-url")
    monkeypatch.delenv("BANK_SYNC_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError):
        crypto.decrypt(ciphertext)


def test_totp_secret_round_trip():
    ciphertext = crypto.encrypt_totp_secret("JBSWY3DPEHPK3PXP")
    assert crypto.decrypt_totp_secret(ciphertext) == "JBSWY3DPEHPK3PXP"


def test_totp_secret_uses_distinct_key_from_bank_sync(monkeypatch):
    # Encrypting under BANK_SYNC_ENCRYPTION_KEY must not be decryptable with
    # TOTP_ENCRYPTION_KEY, and vice versa -- confirms the two domains are
    # actually isolated, not just using two names for the same key.
    bank_ciphertext = crypto.encrypt("some-url")
    totp_ciphertext = crypto.encrypt_totp_secret("JBSWY3DPEHPK3PXP")
    with pytest.raises(ValueError):
        crypto.decrypt_totp_secret(bank_ciphertext)
    with pytest.raises(ValueError):
        crypto.decrypt(totp_ciphertext)


def test_totp_secret_decrypt_with_wrong_key_fails(monkeypatch):
    ciphertext = crypto.encrypt_totp_secret("JBSWY3DPEHPK3PXP")
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(ValueError):
        crypto.decrypt_totp_secret(ciphertext)


def test_totp_secret_without_key_raises(monkeypatch):
    monkeypatch.delenv("TOTP_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError):
        crypto.encrypt_totp_secret("JBSWY3DPEHPK3PXP")
