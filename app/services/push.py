"""Web Push sending (RFC 8030/8291) via pywebpush. Read lazily like app.crypto's
encryption keys — nothing calls this until a user has subscribed, so an unset
VAPID key pair shouldn't break app startup for everyone else.

Requires PUSH_VAPID_PUBLIC_KEY / PUSH_VAPID_PRIVATE_KEY in the environment,
generated once via:
    .venv/bin/python -m app.services.push
Never regenerate once real subscriptions exist -- it invalidates every one of them.
"""
import json
import os
import sqlite3

from pywebpush import WebPushException, webpush

from app.repositories import push_subscriptions as push_subscriptions_repo


def get_public_key() -> str:
    key = os.environ.get("PUSH_VAPID_PUBLIC_KEY")
    if not key:
        raise RuntimeError("PUSH_VAPID_PUBLIC_KEY is not set.")
    return key


def _private_key() -> str:
    key = os.environ.get("PUSH_VAPID_PRIVATE_KEY")
    if not key:
        raise RuntimeError("PUSH_VAPID_PRIVATE_KEY is not set.")
    return key


def _claims_email() -> str:
    return os.environ.get("PUSH_VAPID_CLAIMS_EMAIL", "admin@localhost")


def send_to_subscription(
    conn: sqlite3.Connection, subscription: sqlite3.Row, title: str, body: str, url: str = "/"
) -> bool:
    """Sends one push; prunes the subscription row on a 404/410 (expired or
    revoked at the OS level -- expected/routine, not an error to alert on).
    Returns whether the send succeeded."""
    subscription_info = {
        "endpoint": subscription["endpoint"],
        "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]},
    }
    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps({"title": title, "body": body, "url": url}),
            vapid_private_key=_private_key(),
            vapid_claims={"sub": f"mailto:{_claims_email()}"},
        )
        return True
    except WebPushException as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status in (404, 410):
            push_subscriptions_repo.delete_by_endpoint(conn, subscription["endpoint"])
            return False
        raise


def send_to_user(conn: sqlite3.Connection, user_id: int, title: str, body: str, url: str = "/") -> int:
    """Sends to every subscription (device/browser) this user has. Returns how many succeeded."""
    sent = 0
    for subscription in push_subscriptions_repo.list_for_user(conn, user_id):
        if send_to_subscription(conn, subscription, title, body, url):
            sent += 1
    return sent


def generate_keys() -> tuple[str, str]:
    """Generates a new VAPID keypair, returned as (public_key, private_key) base64url
    strings ready to paste into secrets.env. Public key is the raw uncompressed EC
    point (what browsers' pushManager.subscribe applicationServerKey expects);
    private key is the raw 32-byte scalar (what py_vapid's from_raw/pywebpush expect)."""
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid02
    from py_vapid.utils import b64urlencode

    vapid = Vapid02()
    vapid.generate_keys()
    public_bytes = vapid.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    private_value = vapid.private_key.private_numbers().private_value
    private_bytes = private_value.to_bytes(32, "big")
    return b64urlencode(public_bytes), b64urlencode(private_bytes)


if __name__ == "__main__":
    public_key, private_key = generate_keys()
    print(f"PUSH_VAPID_PUBLIC_KEY={public_key}")
    print(f"PUSH_VAPID_PRIVATE_KEY={private_key}")
