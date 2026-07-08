import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.environ.get("BUDGET_DB_PATH", DATA_DIR / "budget.db"))

# Local dev runs over plain HTTP; the VPS deployment is HTTPS-only behind nginx.
# Secure cookies are the default and must be explicitly opted out of for local dev.
SECURE_COOKIES = os.environ.get("BUDGET_INSECURE_COOKIES", "0") != "1"

# Read lazily by app.services.digest, same reasoning as app.crypto's encryption key:
# nothing calls this until a user opts into the digest, so an unset key shouldn't
# break app startup for everyone else.
RESEND_API_KEY = os.environ.get("BUDGET_RESEND_API_KEY")
DIGEST_FROM_EMAIL = os.environ.get("BUDGET_DIGEST_FROM_EMAIL", "onboarding@resend.dev")

# Where the /login "request access" form's notification is delivered. Read lazily by
# app.services.request_access, same reasoning as the digest key above.
REQUEST_ACCESS_TO_EMAIL = os.environ.get("BUDGET_REQUEST_ACCESS_TO_EMAIL")
