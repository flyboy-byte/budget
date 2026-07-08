import json

import pytest
from pywebpush import WebPushException

from app.repositories import push_subscriptions as repo
from app.services import push


@pytest.fixture(autouse=True)
def _vapid_env(monkeypatch):
    monkeypatch.setenv("PUSH_VAPID_PUBLIC_KEY", "test-public-key")
    monkeypatch.setenv("PUSH_VAPID_PRIVATE_KEY", "test-private-key")


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


def _make_subscription(db, user_id, endpoint="https://push.example/1"):
    repo.create(db, user_id, endpoint, "p256dh-key", "auth-key")
    db.commit()
    return repo.list_for_user(db, user_id)[0]


def test_get_public_key_returns_env_value():
    assert push.get_public_key() == "test-public-key"


def test_get_public_key_raises_when_unset(monkeypatch):
    monkeypatch.delenv("PUSH_VAPID_PUBLIC_KEY", raising=False)
    with pytest.raises(RuntimeError):
        push.get_public_key()


def test_send_to_subscription_calls_webpush_with_payload(monkeypatch, db, user_id):
    sub = _make_subscription(db, user_id)
    calls = []

    def fake_webpush(subscription_info, data, vapid_private_key, vapid_claims):
        calls.append((subscription_info, json.loads(data), vapid_private_key, vapid_claims))

    monkeypatch.setattr(push, "webpush", fake_webpush)
    result = push.send_to_subscription(db, sub, "Title", "Body", url="/today")

    assert result is True
    assert len(calls) == 1
    subscription_info, payload, private_key, claims = calls[0]
    assert subscription_info["endpoint"] == sub["endpoint"]
    assert payload == {"title": "Title", "body": "Body", "url": "/today"}
    assert private_key == "test-private-key"
    assert claims["sub"].startswith("mailto:")


def test_send_to_subscription_prunes_on_410(monkeypatch, db, user_id):
    sub = _make_subscription(db, user_id)

    def fake_webpush(**kwargs):
        raise WebPushException("gone", response=_FakeResponse(410))

    monkeypatch.setattr(push, "webpush", fake_webpush)
    result = push.send_to_subscription(db, sub, "Title", "Body")

    assert result is False
    assert repo.list_for_user(db, user_id) == []


def test_send_to_subscription_reraises_other_errors(monkeypatch, db, user_id):
    sub = _make_subscription(db, user_id)

    def fake_webpush(**kwargs):
        raise WebPushException("server error", response=_FakeResponse(500))

    monkeypatch.setattr(push, "webpush", fake_webpush)
    with pytest.raises(WebPushException):
        push.send_to_subscription(db, sub, "Title", "Body")
    assert len(repo.list_for_user(db, user_id)) == 1


def test_send_to_user_sends_to_every_subscription(monkeypatch, db, user_id):
    _make_subscription(db, user_id, "https://push.example/1")
    _make_subscription(db, user_id, "https://push.example/2")
    sent = []

    def fake_webpush(**kwargs):
        sent.append(kwargs["subscription_info"]["endpoint"])

    monkeypatch.setattr(push, "webpush", fake_webpush)
    count = push.send_to_user(db, user_id, "Title", "Body")

    assert count == 2
    assert sorted(sent) == ["https://push.example/1", "https://push.example/2"]


def test_generate_keys_returns_usable_keypair():
    public_key, private_key = push.generate_keys()
    assert isinstance(public_key, str) and len(public_key) > 0
    assert isinstance(private_key, str) and len(private_key) > 0
