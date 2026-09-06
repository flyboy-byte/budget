"""Hand-rolled inline SVG sparkline — no JS charting library (explicit project
non-goal), no CDN, no build step. Server-renders a small trend line + area wash +
end-dot, colored by whether the latest value is good (>=0) or bad (<0), with
native <title> tooltips on each point (zero JS, works on hover and keyboard focus).

SECURITY: the returned string is rendered with Jinja's `| safe` filter (it has to
be -- it's markup), which means autoescaping is bypassed and *this module is solely
responsible for escaping its own inputs*. Snapshot dates are server-generated in
normal operation, but `POST /export/restore` writes attacker-supplied strings from
an uploaded backup straight into `snapshots.snapshot_date`, so they are NOT
trustworthy. Every interpolated value goes through `_esc()`. Confirmed exploitable
before this was added (2026-09-06): a crafted backup broke out of the aria-label
attribute and executed script under the production CSP, which permits
'unsafe-inline' and therefore blocks nothing here.
"""
from html import escape

from app.money import format_cents


def _esc(value: object) -> str:
    """Escape for both element text and double-quoted attribute contexts."""
    return escape(str(value), quote=True)

_WIDTH = 320
_HEIGHT = 64
_PAD = 8


def _split_index(points: list[tuple[str, int]], last_real_date: str | None) -> int | None:
    """Index of the last point on or before last_real_date -- the boundary between
    real snapshots and repeated-frozen-number ones. None if there's nothing to split
    (no last_real_date given, or every point is already on/before it)."""
    if last_real_date is None:
        return None
    candidates = [i for i, (d, _) in enumerate(points) if d <= last_real_date]
    if not candidates:
        return None
    split = candidates[-1]
    return split if split < len(points) - 1 else None


def build_sparkline_svg(points: list[tuple[str, int]], last_real_date: str | None = None) -> str:
    """points: [(iso_date, cents), ...] ascending by date. Returns an <svg> string,
    or "" if there's nothing to plot.

    last_real_date (STATE_SPEC.md's stale-sparkline treatment): the last date any
    account/debt actually changed. Points after it are the same frozen number
    re-captured by the daily auto-snapshot, not a real trend -- rendered dashed, with
    a hollow marker at the last real point, instead of implying the value kept moving.
    """
    if not points:
        return ""

    values = [p[1] for p in points]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    n = len(values)

    def x(i: int) -> float:
        if n == 1:
            return _WIDTH / 2
        return _PAD + (_WIDTH - 2 * _PAD) * (i / (n - 1))

    def y(v: int) -> float:
        return _PAD + (_HEIGHT - 2 * _PAD) * (1 - (v - lo) / span)

    coords = [(x(i), y(v)) for i, v in enumerate(values)]
    latest = values[-1]
    color = "var(--good)" if latest >= 0 else "var(--bad)"

    if n == 1:
        cx, cy = coords[0]
        return (
            f'<svg viewBox="0 0 {_WIDTH} {_HEIGHT}" width="100%" height="{_HEIGHT}" '
            f'role="img" aria-label="Safe-to-spend trend, single data point">'
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" fill="{color}" '
            f'stroke="var(--bg-elevated)" stroke-width="2"/></svg>'
        )

    path_d = "M " + " L ".join(f"{cx:.1f},{cy:.1f}" for cx, cy in coords)
    baseline_y = y(0) if lo <= 0 <= hi else _HEIGHT - _PAD
    area_d = f"{path_d} L {coords[-1][0]:.1f},{baseline_y:.1f} L {coords[0][0]:.1f},{baseline_y:.1f} Z"
    end_x, end_y = coords[-1]

    hit_targets = "".join(
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="10" fill="transparent">'
        f"<title>{_esc(date)} — {_esc(format_cents(v))}</title></circle>"
        for (cx, cy), (date, v) in zip(coords, points)
    )

    split = _split_index(points, last_real_date)
    if split is None:
        stroke_paths = (
            f'<path d="{path_d}" fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round"/>'
        )
        hollow_marker = ""
    else:
        solid_d = "M " + " L ".join(f"{cx:.1f},{cy:.1f}" for cx, cy in coords[: split + 1])
        dashed_d = "M " + " L ".join(f"{cx:.1f},{cy:.1f}" for cx, cy in coords[split:])
        stroke_paths = (
            f'<path d="{solid_d}" fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round"/>'
            f'<path d="{dashed_d}" fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round" stroke-dasharray="4 3"/>'
        )
        split_x, split_y = coords[split]
        hollow_marker = (
            f'<circle cx="{split_x:.1f}" cy="{split_y:.1f}" r="4" fill="var(--bg-elevated)" '
            f'stroke="{color}" stroke-width="2"/>'
        )

    return (
        f'<svg viewBox="0 0 {_WIDTH} {_HEIGHT}" width="100%" height="{_HEIGHT}" '
        f'role="img" aria-label="Safe-to-spend trend, {_esc(points[0][0])} to {_esc(points[-1][0])}">'
        f'<path d="{area_d}" fill="{color}" fill-opacity="0.12" stroke="none"/>'
        f"{stroke_paths}"
        f"{hollow_marker}"
        f'<circle cx="{end_x:.1f}" cy="{end_y:.1f}" r="4" fill="{color}" '
        f'stroke="var(--bg-elevated)" stroke-width="2"/>'
        f"{hit_targets}"
        f"</svg>"
    )
