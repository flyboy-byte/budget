"""QR code for the TOTP enrollment flow. Uses segno (pure Python, zero
dependencies, no CDN) to render a data: URI directly embeddable as an <img src>,
so there's no separate image route and no external request — same self-contained
spirit as app/sparkline.py's hand-rolled inline SVG, just via a data URI instead of
a raw inline <svg> (simpler and more robust than stripping segno's XML declaration
for true inline embedding).
"""
import segno


def build_qr_data_uri(uri: str) -> str:
    return segno.make(uri, error="m").svg_data_uri(scale=4)
