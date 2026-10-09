"""HTTP API for API Gateway (HTTP API, payload v2). Contract: openapi.yaml (spec 07)."""

from __future__ import annotations

import base64
import json
import logging
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, Mapping

from .. import alerts, b2b, keys, ledger, pricing, products, suppliers, views
from ..channels import shopify
from ..channels.sync import CHANNELS, link_listing, listings_for, skus_on_channel, unlink_listing
from ..context import Ctx
from ..errors import Forbidden, NotFound, SimmercaError, ValidationError
from ..intelligence.extractor import approve_attributes, review_queue
from ..whatsapp.flow import handle_inbound
from ..whatsapp.parser import LlmFallback

log = logging.getLogger(__name__)


@dataclass
class App:
    ctx: Ctx
    adapters: Mapping = field(default_factory=dict)
    secrets: Mapping[str, str] = field(default_factory=dict)
    llm: LlmFallback | None = None
    public_base_url: str | None = None


@dataclass
class Request:
    method: str
    path: str
    params: dict
    query: dict
    headers: dict
    raw_body: bytes
    user: dict

    def json(self) -> dict:
        if not self.raw_body:
            return {}
        try:
            data = json.loads(self.raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValidationError("body must be JSON") from None
        if not isinstance(data, dict):
            raise ValidationError("body must be a JSON object")
        return data

    @property
    def actor(self) -> str:
        return f"admin:{self.user.get('email') or self.user.get('sub', 'unknown')}"


ROUTES: list[tuple[str, re.Pattern, str | None, Callable]] = []


def route(method: str, pattern: str, group: str | None):
    regex = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$")

    def deco(fn):
        ROUTES.append((method, regex, group, fn))
        return fn

    return deco


# ------------------------------------------------------------------ entry


def handle(app: App, event: dict) -> dict:
    try:
        req = _request(event)
        for method, regex, group, fn in ROUTES:
            m = regex.match(req.path)
            if m and method == req.method:
                if group and group not in req.user.get("groups", []):
                    raise Forbidden("not allowed")
                req.params = m.groupdict()
                result = fn(app, req)
                if isinstance(result, tuple) and len(result) == 3:
                    return {"statusCode": result[0], "headers": result[1], "body": result[2]}
                status, body = result if isinstance(result, tuple) else (200, result)
                return _json(status, body)
        return _json(404, {"error": "not found"})
    except SimmercaError as e:
        return _json(e.status, {"error": str(e)})
    except Exception:  # noqa: BLE001
        log.exception("unhandled error")
        return _json(500, {"error": "internal error"})


def _request(event: dict) -> Request:
    http = (event.get("requestContext") or {}).get("http") or {}
    body = event.get("body") or ""
    raw = base64.b64decode(body) if event.get("isBase64Encoded") else body.encode("utf-8")
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    claims = (((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt") or {}).get("claims") or {}
    groups = claims.get("cognito:groups") or []
    if isinstance(groups, str):  # HTTP API serializes array claims as "[a b]"
        groups = [g for g in re.split(r"[\s,\[\]]+", groups) if g]
    user = {"sub": claims.get("sub"), "email": claims.get("email"), "groups": list(groups)}
    return Request(
        method=http.get("method", "GET").upper(),
        path=event.get("rawPath") or http.get("path", "/"),
        params={},
        query=event.get("queryStringParameters") or {},
        headers=headers,
        raw_body=raw,
        user=user,
    )


def _json(status: int, body) -> dict:
    return {"statusCode": status, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body, default=str, ensure_ascii=False)}


def _strip(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in ("pk", "sk")}


# ------------------------------------------------------------------ public


@route("GET", "/public/products", None)
def public_list(app: App, req: Request):
    ctx = app.ctx
    visible = set(skus_on_channel(ctx, "aalora"))
    items = [views.public_product(ctx, p) for p in products.list_products(ctx, status="active") if p["sku"] in visible]
    fabric = req.query.get("fabric")
    if fabric:
        items = [i for i in items if i["attributes"].get("fabric") == fabric]
    return {"items": items}


@route("GET", "/public/products/{sku}", None)
def public_get(app: App, req: Request):
    ctx = app.ctx
    p = products.find_product(ctx, req.params["sku"])
    if not p or p.get("status") != "active" or "aalora" not in p.get("channels", []):
        raise NotFound("product not found")
    return views.public_product(ctx, p)


# ------------------------------------------------------------------ b2b


def _approved_buyer(app: App, req: Request) -> dict:
    buyer = b2b.get_buyer(app.ctx, req.user["sub"])
    if buyer.get("status") != "approved":
        raise Forbidden("buyer account is not approved yet")
    return buyer


@route("GET", "/b2b/me", "buyer")
def b2b_me(app: App, req: Request):
    return views.buyer_view(b2b.get_buyer(app.ctx, req.user["sub"]))


@route("PUT", "/b2b/me", "buyer")
def b2b_me_put(app: App, req: Request):
    data = req.json()
    data.setdefault("email", req.user.get("email", ""))
    return views.buyer_view(b2b.upsert_buyer_profile(app.ctx, req.user["sub"], data))


@route("GET", "/b2b/catalog", "buyer")
def b2b_catalog(app: App, req: Request):
    _approved_buyer(app, req)
    return {"items": [views.b2b_product(app.ctx, p) for p in products.list_products(app.ctx, status="active")]}


@route("POST", "/b2b/quotes", "buyer")
def b2b_quote_create(app: App, req: Request):
    data = req.json()
    quote = b2b.create_quote(app.ctx, req.user["sub"], data.get("lines") or [], data.get("note", ""))
    return 201, views.quote_view(quote)


@route("GET", "/b2b/quotes", "buyer")
def b2b_quotes(app: App, req: Request):
    return {"items": [views.quote_view(q) for q in b2b.list_quotes(app.ctx, buyer_id=req.user["sub"])]}


@route("GET", "/b2b/quotes/{quote_id}", "buyer")
def b2b_quote_get(app: App, req: Request):
    return views.quote_view(b2b.get_quote(app.ctx, req.params["quote_id"], buyer_id=req.user["sub"]))


# ------------------------------------------------------------------ admin: products & stock


@route("GET", "/admin/dashboard", "admin")
def admin_dashboard(app: App, req: Request):
    ctx = app.ctx
    all_products = products.list_products(ctx)
    low = []
    for p in all_products:
        if p.get("status") == "active" and not p.get("made_to_order") and ledger.balance(ctx, p["sku"]) <= 1:
            low.append(p["sku"])
    return {
        "products": {s: sum(1 for p in all_products if p.get("status") == s) for s in products.STATUSES},
        "low_stock_skus": low,
        "pending_attribute_reviews": len(review_queue(ctx)),
        "pending_price_proposals": len(pricing.list_proposals(ctx)),
        "open_alerts": len(alerts.list_alerts(ctx)),
        "requested_quotes": len(b2b.list_quotes(ctx, status="requested")),
        "pending_buyers": len(b2b.list_buyers(ctx, status="pending")),
    }


@route("GET", "/admin/products", "admin")
def admin_products(app: App, req: Request):
    ctx = app.ctx
    rows = products.list_products(ctx, status=req.query.get("status"), supplier_id=req.query.get("supplier_id"))
    return {"items": [dict(_strip(p), on_hand=ledger.balance(ctx, p["sku"])) for p in rows]}


@route("POST", "/admin/products", "admin")
def admin_product_create(app: App, req: Request):
    return 201, _strip(products.create_product(app.ctx, req.json(), req.actor))


@route("GET", "/admin/products/{sku}", "admin")
def admin_product_get(app: App, req: Request):
    ctx = app.ctx
    p = products.get_product(ctx, req.params["sku"])
    return dict(_strip(p), on_hand=ledger.balance(ctx, p["sku"]), availability=products.availability(ctx, p),
                listings=[_strip(lst) for lst in listings_for(ctx, p["sku"])],
                image_urls=[ctx.blobs.presign_get(k) for k in p.get("images", [])])


@route("PATCH", "/admin/products/{sku}", "admin")
def admin_product_patch(app: App, req: Request):
    return _strip(products.update_product(app.ctx, req.params["sku"], req.json(), req.actor))


@route("POST", "/admin/products/{sku}/stock", "admin")
def admin_stock(app: App, req: Request):
    data = req.json()
    sku = req.params["sku"]
    idem = data.get("idempotency_key") or req.headers.get("idempotency-key")
    if not idem:
        raise ValidationError("idempotency_key (or Idempotency-Key header) is required")
    if "count" in data:
        return ledger.set_count(app.ctx, sku, int(data["count"]), "admin", req.actor, f"admin:{idem}")
    delta = data.get("delta")
    if not isinstance(delta, int) or isinstance(delta, bool):
        raise ValidationError("delta must be an integer")
    return ledger.record_change(app.ctx, sku, delta, data.get("reason", "ADJUST"), "admin", req.actor,
                                f"admin:{idem}", ref=data.get("note"))


@route("GET", "/admin/products/{sku}/ledger", "admin")
def admin_ledger(app: App, req: Request):
    return {"items": [_strip(e) for e in ledger.events(app.ctx, req.params["sku"], int(req.query.get("limit", 100)))]}


@route("GET", "/admin/products/{sku}/reconcile", "admin")
def admin_reconcile(app: App, req: Request):
    return ledger.reconcile(app.ctx, req.params["sku"])


@route("POST", "/admin/products/{sku}/images", "admin")
def admin_image_upload(app: App, req: Request):
    ctx = app.ctx
    data = req.json()
    content_type = data.get("content_type", "image/jpeg")
    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValidationError("content_type must be image/jpeg, image/png or image/webp")
    p = products.get_product(ctx, req.params["sku"])
    ext = content_type.split("/")[1].replace("jpeg", "jpg")
    key = f"{ctx.tenant}/products/{p['sku']}/{ctx.ids()}.{ext}"
    p["images"] = list(p.get("images", [])) + [key]
    ctx.store.put(p)
    return 201, {"key": key, "upload_url": ctx.blobs.presign_put(key, content_type), "content_type": content_type}


@route("POST", "/admin/products/{sku}/intelligence", "admin")
def admin_intelligence(app: App, req: Request):
    products.get_product(app.ctx, req.params["sku"])
    (app.ctx.intelligence_queue or app.ctx.queue).send(
        {"type": "propose", "tenant": app.ctx.tenant, "sku": req.params["sku"]})
    return 202, {"queued": True}


@route("POST", "/admin/products/{sku}/attributes/approve", "admin")
def admin_attr_approve(app: App, req: Request):
    return _strip(approve_attributes(app.ctx, req.params["sku"], req.actor, req.json().get("edits")))


@route("GET", "/admin/review-queue", "admin")
def admin_review_queue(app: App, req: Request):
    ctx = app.ctx
    return {"items": [dict(_strip(p), image_urls=[ctx.blobs.presign_get(k) for k in p.get("images", [])])
                      for p in review_queue(ctx)]}


# ------------------------------------------------------------------ admin: pricing


@route("GET", "/admin/products/{sku}/price-suggestion", "admin")
def admin_price_suggest(app: App, req: Request):
    return pricing.suggest_price(app.ctx, req.params["sku"])


@route("POST", "/admin/products/{sku}/price-proposals", "admin")
def admin_price_propose(app: App, req: Request):
    data = req.json()
    return 201, _strip(pricing.propose_price(app.ctx, req.params["sku"], data.get("price_cents"), req.actor,
                                             data.get("reason", "")))


@route("GET", "/admin/price-proposals", "admin")
def admin_price_list(app: App, req: Request):
    status = req.query.get("status", "pending")
    return {"items": [_strip(p) for p in pricing.list_proposals(app.ctx, None if status == "all" else status)]}


@route("POST", "/admin/price-proposals/{pid}/approve", "admin")
def admin_price_approve(app: App, req: Request):
    return _strip(pricing.approve_proposal(app.ctx, req.params["pid"], req.actor))


@route("POST", "/admin/price-proposals/{pid}/reject", "admin")
def admin_price_reject(app: App, req: Request):
    return _strip(pricing.reject_proposal(app.ctx, req.params["pid"], req.actor))


@route("POST", "/admin/comparables", "admin")
def admin_comparable(app: App, req: Request):
    return 201, _strip(pricing.add_comparable(app.ctx, req.json(), req.actor))


# ------------------------------------------------------------------ admin: suppliers, channels, ops


@route("GET", "/admin/suppliers", "admin")
def admin_suppliers(app: App, req: Request):
    return {"items": [_strip(s) for s in suppliers.list_suppliers(app.ctx)]}


@route("POST", "/admin/suppliers", "admin")
def admin_supplier_create(app: App, req: Request):
    return 201, _strip(suppliers.create_supplier(app.ctx, req.json()))


@route("PATCH", "/admin/suppliers/{supplier_id}", "admin")
def admin_supplier_patch(app: App, req: Request):
    data = req.json()
    if "active" not in data:
        raise ValidationError("only 'active' can be changed here")
    return _strip(suppliers.set_active(app.ctx, req.params["supplier_id"], bool(data["active"])))


@route("POST", "/admin/products/{sku}/listings", "admin")
def admin_listing_link(app: App, req: Request):
    data = req.json()
    item = link_listing(app.ctx, req.params["sku"], data.get("channel", ""), app.adapters, data.get("external"),
                        req.actor)
    return 201, _strip(item)


@route("DELETE", "/admin/products/{sku}/listings/{channel}", "admin")
def admin_listing_unlink(app: App, req: Request):
    if req.params["channel"] not in CHANNELS:
        raise ValidationError("unknown channel")
    unlink_listing(app.ctx, req.params["sku"], req.params["channel"])
    return {"unlinked": True}


@route("POST", "/admin/products/{sku}/sync", "admin")
def admin_sync(app: App, req: Request):
    products.get_product(app.ctx, req.params["sku"])
    for kind in ("push_stock", "push_price"):
        app.ctx.queue.send({"type": kind, "tenant": app.ctx.tenant, "sku": req.params["sku"]})
    return 202, {"queued": True}


@route("GET", "/admin/alerts", "admin")
def admin_alerts(app: App, req: Request):
    status = req.query.get("status", "open")
    return {"items": [_strip(a) for a in alerts.list_alerts(app.ctx, None if status == "all" else status)]}


@route("POST", "/admin/alerts/{alert_id}/resolve", "admin")
def admin_alert_resolve(app: App, req: Request):
    return _strip(alerts.resolve_alert(app.ctx, req.params["alert_id"]))


@route("GET", "/admin/production-orders", "admin")
def admin_production_orders(app: App, req: Request):
    return {"items": [_strip(o) for o in app.ctx.store.query(keys.kind_pk(app.ctx.tenant, "PRODORDER"))]}


# ------------------------------------------------------------------ admin: b2b


@route("GET", "/admin/buyers", "admin")
def admin_buyers(app: App, req: Request):
    return {"items": [_strip(b) for b in b2b.list_buyers(app.ctx, req.query.get("status"))]}


@route("POST", "/admin/buyers/{buyer_id}/status", "admin")
def admin_buyer_status(app: App, req: Request):
    return _strip(b2b.set_buyer_status(app.ctx, req.params["buyer_id"], req.json().get("status", ""), req.actor))


@route("GET", "/admin/quotes", "admin")
def admin_quotes(app: App, req: Request):
    return {"items": [_strip(q) for q in b2b.list_quotes(app.ctx, status=req.query.get("status"))]}


@route("POST", "/admin/quotes/{quote_id}/approve", "admin")
def admin_quote_approve(app: App, req: Request):
    data = req.json()
    return _strip(b2b.approve_quote(app.ctx, req.params["quote_id"], req.actor, data.get("price_overrides"),
                                    data.get("message", "")))


@route("POST", "/admin/quotes/{quote_id}/reject", "admin")
def admin_quote_reject(app: App, req: Request):
    return _strip(b2b.reject_quote(app.ctx, req.params["quote_id"], req.actor, req.json().get("message", "")))


# ------------------------------------------------------------------ webhooks


@route("POST", "/webhooks/whatsapp/{token}", None)
def webhook_whatsapp(app: App, req: Request):
    params = {k: v[0] if len(v) == 1 else v
              for k, v in urllib.parse.parse_qs(req.raw_body.decode("utf-8"), keep_blank_values=True).items()}
    base = app.public_base_url or f"https://{req.headers.get('host', '')}"
    url = base.rstrip("/") + req.path
    status, body = handle_inbound(app.ctx, path_token=req.params["token"], url=url, params=params,
                                  signature=req.headers.get("x-twilio-signature"), secrets=app.secrets, llm=app.llm)
    ctype = "application/xml" if status == 200 else "text/plain"
    return status, {"Content-Type": ctype}, body


@route("POST", "/webhooks/shopify", None)
def webhook_shopify(app: App, req: Request):
    if not shopify.verify_webhook(req.raw_body, req.headers.get("x-shopify-hmac-sha256"),
                                  app.secrets.get("shopify_webhook_secret", "")):
        return 401, {"error": "invalid signature"}
    topic = req.headers.get("x-shopify-topic", "")
    if topic != "orders/create":
        return {"ignored": topic}
    order = shopify.parse_order_webhook(json.loads(req.raw_body.decode("utf-8")))
    from ..channels.sync import ingest_order

    return {"result": ingest_order(app.ctx, order)}
