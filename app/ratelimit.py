"""In-memory sliding-window rate limiter.

Deliberately not backed by a table or Redis: this app runs as a single uvicorn process
(see systemd unit — no multi-worker config), so an in-process dict is sufficient and the
limiter resetting on restart/deploy is an acceptable tradeoff for a solo/family-scale app.
Nginx also rate-limits /login at the reverse-proxy layer on the VPS deployment; this is
defense-in-depth for any deployment (including local dev) that doesn't sit behind nginx.
"""
import time

_attempts: dict[str, list[float]] = {}


def record_failure(key: str) -> None:
    _attempts.setdefault(key, []).append(time.monotonic())


def reset(key: str) -> None:
    _attempts.pop(key, None)


def is_limited(key: str, *, max_attempts: int = 5, window_seconds: float = 300) -> bool:
    """Merely checking a key (e.g. every request from a scanning bot that never even
    submits credentials) must never create a permanent entry — only record_failure does,
    and expired entries are evicted entirely rather than left as empty lists, so this
    dict stays bounded by "distinct IPs with a recent failure," not "every IP ever seen."
    """
    now = time.monotonic()
    existing = _attempts.get(key)
    if not existing:
        return False

    recent = [t for t in existing if now - t < window_seconds]
    if recent:
        _attempts[key] = recent
    else:
        del _attempts[key]
    return len(recent) >= max_attempts
