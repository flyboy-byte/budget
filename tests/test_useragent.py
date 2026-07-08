from app.useragent import describe_user_agent


def test_none_returns_unknown():
    assert describe_user_agent(None) == "Unknown device"


def test_empty_string_returns_unknown():
    assert describe_user_agent("") == "Unknown device"


def test_chrome_on_mac():
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    assert describe_user_agent(ua) == "Chrome on Mac"


def test_brave_on_windows():
    # Brave's UA still contains Chrome/ and Safari/ tokens; Brave/ must win
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Brave/125"
    assert describe_user_agent(ua) == "Brave on Windows"


def test_safari_on_iphone():
    ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    assert describe_user_agent(ua) == "Safari on iPhone"


def test_firefox_on_linux():
    ua = "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0"
    assert describe_user_agent(ua) == "Firefox on Linux"


def test_unrecognized_ua_still_returns_something():
    assert describe_user_agent("some-weird-bot/1.0") == "Unknown browser"
