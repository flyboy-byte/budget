from app import ratelimit


def test_not_limited_below_threshold():
    key = "test-key-1"
    for _ in range(4):
        ratelimit.record_failure(key)
    assert not ratelimit.is_limited(key, max_attempts=5, window_seconds=300)


def test_limited_at_threshold():
    key = "test-key-2"
    for _ in range(5):
        ratelimit.record_failure(key)
    assert ratelimit.is_limited(key, max_attempts=5, window_seconds=300)


def test_reset_clears_attempts():
    key = "test-key-3"
    for _ in range(5):
        ratelimit.record_failure(key)
    ratelimit.reset(key)
    assert not ratelimit.is_limited(key, max_attempts=5, window_seconds=300)


def test_old_attempts_outside_window_dont_count():
    key = "test-key-4"
    for _ in range(5):
        ratelimit.record_failure(key)
    assert not ratelimit.is_limited(key, max_attempts=5, window_seconds=0)


def test_checking_an_unknown_key_does_not_create_an_entry():
    key = "test-key-5"
    ratelimit.reset(key)
    assert not ratelimit.is_limited(key)
    assert key not in ratelimit._attempts


def test_expired_entry_is_evicted_not_left_as_empty_list():
    key = "test-key-6"
    ratelimit.record_failure(key)
    assert not ratelimit.is_limited(key, max_attempts=5, window_seconds=0)
    assert key not in ratelimit._attempts


def test_keys_are_independent():
    ratelimit.reset("key-a")
    ratelimit.reset("key-b")
    for _ in range(5):
        ratelimit.record_failure("key-a")
    assert ratelimit.is_limited("key-a", max_attempts=5, window_seconds=300)
    assert not ratelimit.is_limited("key-b", max_attempts=5, window_seconds=300)
