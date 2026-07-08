"""Best-effort, dependency-free "which browser/OS is this" label for the sessions
list. Not trying to be a real UA parser — just enough to tell your phone apart from
your laptop in a list of active sessions.
"""

_BROWSERS = [
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("Brave/", "Brave"),
    ("Chrome/", "Chrome"),
    ("CriOS/", "Chrome"),
    ("Firefox/", "Firefox"),
    ("FxiOS/", "Firefox"),
    ("Safari/", "Safari"),
]

_OS_MARKERS = [
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("Mac OS X", "Mac"),
    ("Windows", "Windows"),
    ("Linux", "Linux"),
]


def describe_user_agent(user_agent: str | None) -> str:
    if not user_agent:
        return "Unknown device"

    # _BROWSERS is ordered Edge/Opera/Brave/Chrome before Safari deliberately —
    # every Chromium browser's UA string also contains "Safari/", so checking
    # Safari last (not a separate fixup pass) is what keeps Chrome/Brave/Edge from
    # being mislabeled as Safari.
    browser = next((name for token, name in _BROWSERS if token in user_agent), "Unknown browser")
    os_name = next((name for marker, name in _OS_MARKERS if marker in user_agent), None)

    return f"{browser} on {os_name}" if os_name else browser
