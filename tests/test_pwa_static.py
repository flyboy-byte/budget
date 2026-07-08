from fastapi.testclient import TestClient

from app.main import app


def test_manifest_served_with_correct_content_type():
    client = TestClient(app)
    resp = client.get("/static/manifest.webmanifest")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/manifest+json")


def test_service_worker_served_at_root_with_js_content_type():
    # Must be served at the site root, not /static/sw.js -- a service worker's
    # default scope is the directory it's served from, and /static/ can never
    # cover an actual page.
    client = TestClient(app)
    resp = client.get("/sw.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]


def test_manifest_includes_shortcuts():
    client = TestClient(app)
    resp = client.get("/static/manifest.webmanifest")
    shortcuts = resp.json()["shortcuts"]
    urls = {s["url"] for s in shortcuts}
    assert urls == {"/today", "/committed-purchases/new", "/bank/transactions"}


def test_icons_served():
    client = TestClient(app)
    for name in ("icon-192.png", "icon-512.png", "icon-maskable-512.png"):
        resp = client.get(f"/static/icons/{name}")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
