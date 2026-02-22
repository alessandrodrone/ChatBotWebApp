"""
Verifica firma X-Hub-Signature di Meta.
"""

import hashlib
import hmac
from flask import current_app


def verify_signature(payload: bytes, signature_header: str) -> bool:
    """
    Verifica che il payload provenga da Meta usando HMAC SHA-256.
    signature_header = "sha256=<hex>"
    """
    app_secret = current_app.config.get("META_APP_SECRET", "")
    if not app_secret or not signature_header:
        return False

    if not signature_header.startswith("sha256="):
        return False

    expected = hmac.new(
        app_secret.encode(), payload, hashlib.sha256
    ).hexdigest()

    received = signature_header[7:]
    return hmac.compare_digest(expected, received)
