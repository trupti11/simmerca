"""Twilio webhook security (spec 02, criterion 1)."""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Mapping


def compute_twilio_signature(url: str, params: Mapping[str, str | list[str]], auth_token: str) -> str:
    """Twilio's algorithm: URL + each POST param name+value, names sorted, then HMAC-SHA1, base64."""
    payload = url
    for name in sorted(set(params)):
        raw = params[name]
        values = raw if isinstance(raw, list) else [raw]
        for value in sorted(set(values)):
            payload += name + value
    digest = hmac.new(auth_token.encode(), payload.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def verify_twilio_signature(url: str, params: Mapping[str, str | list[str]], signature: str | None,
                            auth_token: str) -> bool:
    if not signature or not auth_token:
        return False
    expected = compute_twilio_signature(url, params, auth_token)
    return hmac.compare_digest(expected, signature)


def verify_path_token(given: str | None, expected: str | None) -> bool:
    if not given or not expected:
        return False
    return hmac.compare_digest(given.encode(), expected.encode())
