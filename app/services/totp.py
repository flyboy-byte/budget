"""Pure TOTP (RFC 6238) math and secret/URI generation — no DB access, fully
unit-testable without a database. app/routers/settings.py and app/routers/auth.py
own reading/writing the encrypted secret and the replay-protection step.
"""
import time

import pyotp

STEP_SECONDS = 30

# A real, valid-shaped secret nobody's account uses — computed once at import,
# mirroring app/security.py's DUMMY_PASSWORD_HASH. Callers verify against this
# when a real check would otherwise be skipped (no such user, or that user
# doesn't require TOTP), so a login attempt's response time doesn't reveal
# which usernames exist or which have TOTP enabled.
DUMMY_TOTP_SECRET = pyotp.random_base32()


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name="Budget")


def verify_code(secret: str, code: str, last_used_step: int | None) -> int | None:
    """Checks `code` against a +/-1 step window (30s clock-drift tolerance) and
    rejects replay: a step that was already consumed can't be accepted again, even
    if it's still within the current window. Returns the step to persist as the new
    last_used_step on success, or None if the code is invalid or replayed."""
    if not code:
        return None
    totp = pyotp.TOTP(secret)
    current_step = int(time.time() // STEP_SECONDS)
    for step in (current_step - 1, current_step, current_step + 1):
        if last_used_step is not None and step <= last_used_step:
            continue
        if totp.at(step * STEP_SECONDS) == code:
            return step
    return None
