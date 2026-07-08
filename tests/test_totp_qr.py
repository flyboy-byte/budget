from app.totp_qr import build_qr_data_uri


def test_build_qr_data_uri_returns_embeddable_data_uri():
    uri = build_qr_data_uri("otpauth://totp/Budget:alice?secret=ABC&issuer=Budget")
    assert uri.startswith("data:image/svg+xml")
