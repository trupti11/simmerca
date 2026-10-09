"""One HTTP client for every marketplace: timeouts, retries on 429/5xx with backoff (spec 04, criterion 7)."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from ..errors import ChannelError

log = logging.getLogger(__name__)
RETRY_STATUSES = {429, 500, 502, 503, 504}


@dataclass
class HttpResponse:
    status: int
    body: bytes
    headers: dict = field(default_factory=dict)

    def json(self):
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))


Transport = Callable[[str, str, dict, bytes | None, float], HttpResponse]


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> HttpResponse:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https URLs built in code
            return HttpResponse(resp.status, resp.read(), dict(resp.headers))
    except urllib.error.HTTPError as e:
        return HttpResponse(e.code, e.read() or b"", dict(e.headers or {}))


class HttpClient:
    def __init__(self, transport: Transport | None = None, retries: int = 4, backoff: float = 0.5,
                 timeout: float = 15.0, sleep: Callable[[float], None] = time.sleep):
        self.transport = transport or urllib_transport
        self.retries = retries
        self.backoff = backoff
        self.timeout = timeout
        self.sleep = sleep

    def request(self, method: str, url: str, headers: dict | None = None, json_body=None,
                form: dict | None = None, params: dict | None = None) -> HttpResponse:
        hdrs = {"Accept": "application/json", "User-Agent": "simmerca/0.1"}
        hdrs.update(headers or {})
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode()
            hdrs["Content-Type"] = "application/json"
        elif form is not None:
            body = urllib.parse.urlencode(form).encode()
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        if params:
            url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(params)}"

        last = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.transport(method, url, hdrs, body, self.timeout)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = f"network error: {e}"
                resp = None
            if resp is not None:
                if resp.status < 400:
                    return resp
                if resp.status not in RETRY_STATUSES:
                    raise ChannelError(f"{method} {_safe(url)} -> {resp.status}: {resp.body[:300]!r}")
                last = f"{resp.status}"
            if attempt < self.retries:
                delay = self.backoff * (2 ** attempt)
                if resp is not None:
                    retry_after = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
                    if retry_after and str(retry_after).isdigit():
                        delay = max(delay, min(float(retry_after), 30.0))
                log.warning("retrying %s %s after %s (attempt %d)", method, _safe(url), last, attempt + 1)
                self.sleep(delay)
        raise ChannelError(f"{method} {_safe(url)} failed after retries: {last}")


def _safe(url: str) -> str:
    """Strip query strings (may hold tokens) from logged URLs."""
    return url.split("?", 1)[0]
