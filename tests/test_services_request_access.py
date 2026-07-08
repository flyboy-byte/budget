import httpx
import pytest

from app import config
from app.services import request_access


def _mock_client(handler):
    return httpx.MockTransport(handler)


def test_raises_when_api_key_missing(monkeypatch):
    monkeypatch.setattr(config, "RESEND_API_KEY", None)
    monkeypatch.setattr(config, "REQUEST_ACCESS_TO_EMAIL", "admin@example.com")
    with pytest.raises(request_access.RequestAccessError):
        request_access.send_request_access_notification(
            "Alice", "alice@example.com", "hi", transport=_mock_client(lambda r: httpx.Response(200))
        )


def test_raises_when_to_email_missing(monkeypatch):
    monkeypatch.setattr(config, "RESEND_API_KEY", "test-key")
    monkeypatch.setattr(config, "REQUEST_ACCESS_TO_EMAIL", None)
    with pytest.raises(request_access.RequestAccessError):
        request_access.send_request_access_notification(
            "Alice", "alice@example.com", "hi", transport=_mock_client(lambda r: httpx.Response(200))
        )


def test_sends_correct_resend_payload(monkeypatch):
    monkeypatch.setattr(config, "RESEND_API_KEY", "test-key")
    monkeypatch.setattr(config, "DIGEST_FROM_EMAIL", "budget@example.com")
    monkeypatch.setattr(config, "REQUEST_ACCESS_TO_EMAIL", "admin@example.com")

    captured = {}

    def handler(request):
        captured["auth"] = request.headers["authorization"]
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"id": "abc"})

    request_access.send_request_access_notification(
        "Alice", "alice@example.com", "Please add me", transport=_mock_client(handler)
    )
    assert captured["auth"] == "Bearer test-key"
    assert "admin@example.com" in captured["body"]
    assert "budget@example.com" in captured["body"]
    assert "Alice" in captured["body"]
    assert "alice@example.com" in captured["body"]
    assert "Please add me" in captured["body"]


def test_raises_on_non_2xx_response(monkeypatch):
    monkeypatch.setattr(config, "RESEND_API_KEY", "test-key")
    monkeypatch.setattr(config, "REQUEST_ACCESS_TO_EMAIL", "admin@example.com")

    def handler(request):
        return httpx.Response(422, text="Invalid `from` field")

    with pytest.raises(request_access.RequestAccessError):
        request_access.send_request_access_notification(
            "Alice", "alice@example.com", "hi", transport=_mock_client(handler)
        )
