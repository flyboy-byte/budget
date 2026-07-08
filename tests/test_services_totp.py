import time

import pyotp

from app.services import totp

SECRET = "JBSWY3DPEHPK3PXP"


def test_generate_secret_is_valid_base32():
    secret = totp.generate_secret()
    assert len(secret) == 32
    pyotp.TOTP(secret).now()  # doesn't raise


def test_provisioning_uri_includes_username_and_issuer():
    uri = totp.provisioning_uri(SECRET, "alice")
    assert uri.startswith("otpauth://totp/")
    assert "alice" in uri
    assert "Budget" in uri
    assert SECRET in uri


def test_verify_code_accepts_current_code():
    code = pyotp.TOTP(SECRET).now()
    step = totp.verify_code(SECRET, code, last_used_step=None)
    assert step is not None


def test_verify_code_rejects_wrong_code():
    assert totp.verify_code(SECRET, "000000", last_used_step=None) is None


def test_verify_code_rejects_blank_code():
    assert totp.verify_code(SECRET, "", last_used_step=None) is None


def test_verify_code_tolerates_one_step_of_drift():
    current_step = int(time.time() // totp.STEP_SECONDS)
    prev_code = pyotp.TOTP(SECRET).at((current_step - 1) * totp.STEP_SECONDS)
    step = totp.verify_code(SECRET, prev_code, last_used_step=None)
    assert step == current_step - 1


def test_verify_code_rejects_replay_of_already_used_step():
    current_step = int(time.time() // totp.STEP_SECONDS)
    code = pyotp.TOTP(SECRET).now()
    # Already consumed the current step -> the same code must not verify again.
    assert totp.verify_code(SECRET, code, last_used_step=current_step) is None


def test_verify_code_rejects_step_older_than_last_used():
    current_step = int(time.time() // totp.STEP_SECONDS)
    prev_code = pyotp.TOTP(SECRET).at((current_step - 1) * totp.STEP_SECONDS)
    # last_used_step is already ahead of the step this code belongs to.
    assert totp.verify_code(SECRET, prev_code, last_used_step=current_step) is None
