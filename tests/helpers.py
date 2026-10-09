"""Shared test fixtures. Tests use unittest so they run with `pytest` or `python -m unittest`."""

from __future__ import annotations

import base64
import itertools
import json
import os
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from simmerca.context import Ctx  # noqa: E402
from simmerca.store import InMemoryBlobStore, InMemoryQueue, InMemoryStore  # noqa: E402
from simmerca.whatsapp.security import compute_twilio_signature  # noqa: E402

TWILIO_TOKEN = "test-twilio-token"
PATH_TOKEN = "path-secret-123"
SHOPIFY_SECRET = "shopify-webhook-secret"
SECRETS = {"twilio_auth_token": TWILIO_TOKEN, "whatsapp_path_token": PATH_TOKEN,
           "shopify_webhook_secret": SHOPIFY_SECRET}
BASE_URL = "https://api.test"


class Clock:
    def __init__(self, start: datetime | None = None):
        self.now = start or datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        self.now += timedelta(microseconds=1)  # strictly increasing timestamps
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)


def make_ctx(**settings) -> Ctx:
    counter = itertools.count(1)
    return Ctx(store=InMemoryStore(), queue=InMemoryQueue(), blobs=InMemoryBlobStore(), tenant="aalora",
               clock=Clock(), ids=lambda: f"id{next(counter):05d}", intelligence_queue=InMemoryQueue(),
               settings=settings)


def seed(ctx: Ctx, stock: int = 3, made_to_order: bool = False, supplier_phone: str = "+919845012345",
         status: str = "active", fabric: str = "Kanjivaram") -> tuple[dict, dict]:
    """Create a supplier and one product with `stock` on hand. Returns (supplier, product)."""
    from simmerca import ledger, products, suppliers

    sup = suppliers.find_by_phone(ctx, supplier_phone) or suppliers.create_supplier(
        ctx, {"name": "Lakshmi Weaver", "phone": supplier_phone, "language": "hi",
              "region": "Arni village, Tiruvannamalai", "craft": "Kanjivaram silk"})
    p = products.create_product(ctx, {
        "fabric": fabric, "title": "Red Kanjivaram with temple border", "supplier_id": sup["id"],
        "cost_cents": 20000, "price_cents": 45000, "currency": "USD", "made_to_order": made_to_order,
        "lead_time_days": 21 if made_to_order else 0, "mto_capacity": 2 if made_to_order else 0,
        "story_public": "Woven on a pit loom in Tamil Nadu over three weeks.",
        "provenance_region": "Kanchipuram, Tamil Nadu", "internal_notes": "weaver asked for advance",
    }, actor="test")
    if stock:
        ledger.record_change(ctx, p["sku"], stock, "RECEIVE", "test", "test", f"seed:{p['sku']}")
    if status != "draft":
        products.update_product(ctx, p["sku"], {"status": status}, actor="test")
    ctx.queue.drain()
    return sup, products.get_product(ctx, p["sku"])


def twilio_params(body: str, sid: str, frm: str = "whatsapp:+919845012345", media: list[str] | None = None) -> dict:
    params = {"MessageSid": sid, "From": frm, "To": "whatsapp:+14155238886", "Body": body,
              "NumMedia": str(len(media or []))}
    for i, url in enumerate(media or []):
        params[f"MediaUrl{i}"] = url
        params[f"MediaContentType{i}"] = "image/jpeg"
    return params


def whatsapp_url(token: str = PATH_TOKEN) -> str:
    return f"{BASE_URL}/webhooks/whatsapp/{token}"


def sign(params: dict, url: str | None = None, token: str = TWILIO_TOKEN) -> str:
    return compute_twilio_signature(url or whatsapp_url(), params, token)


def api_event(method: str, path: str, body=None, groups: list[str] | None = None, sub: str = "user-1",
              headers: dict | None = None, raw: bytes | None = None, query: dict | None = None) -> dict:
    event = {
        "rawPath": path,
        "requestContext": {"http": {"method": method, "path": path}},
        "headers": {"host": "api.test", **(headers or {})},
        "queryStringParameters": query,
        "isBase64Encoded": False,
    }
    if raw is not None:
        event["body"] = base64.b64encode(raw).decode()
        event["isBase64Encoded"] = True
    elif body is not None:
        event["body"] = json.dumps(body)
    if groups is not None:
        event["requestContext"]["authorizer"] = {"jwt": {"claims": {
            "sub": sub, "email": f"{sub}@example.com", "cognito:groups": "[" + " ".join(groups) + "]"}}}
    return event


def form_body(params: dict) -> bytes:
    return urllib.parse.urlencode(params).encode()


class FakeAdapter:
    """Records pushes; can be told to fail."""

    def __init__(self, name: str, fail: bool = False, orders=None):
        self.name = name
        self.fail = fail
        self.calls: list[tuple] = []
        self.orders = orders or []

    def discover_listing(self, sku):
        return {"external_id": f"{self.name}-{sku}"}

    def push_inventory(self, listing, qty):
        from simmerca.errors import ChannelError

        if self.fail:
            raise ChannelError(f"{self.name} down")
        self.calls.append(("inventory", listing["sku"], qty))

    def push_price(self, listing, price_cents, currency):
        self.calls.append(("price", listing["sku"], price_cents, currency))

    def fetch_orders(self, since_iso):
        return self.orders, "2026-10-07T13:00:00Z"
