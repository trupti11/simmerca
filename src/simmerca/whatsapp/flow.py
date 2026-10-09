"""WhatsApp inbound flow (spec 02): verify -> dedup -> allowlist -> parse -> confirm -> ledger."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Mapping

from .. import keys
from ..context import Ctx
from ..errors import InsufficientStock
from ..ledger import balance, record_change, set_count
from ..products import create_product, find_product, list_products
from ..suppliers import find_by_phone
from .parser import LlmFallback, parse
from .replies import reply, twiml
from .security import verify_path_token, verify_twilio_signature

log = logging.getLogger(__name__)
PENDING_TTL = timedelta(minutes=15)
MSGSID_TTL = timedelta(days=7)


def handle_inbound(
    ctx: Ctx,
    *,
    path_token: str | None,
    url: str,
    params: Mapping[str, str],
    signature: str | None,
    secrets: Mapping[str, str],
    llm: LlmFallback | None = None,
) -> tuple[int, str]:
    """Returns (http_status, twiml_body)."""
    # 1. secret path token, 2. Twilio signature: before ANY side effect
    if not verify_path_token(path_token, secrets.get("whatsapp_path_token")):
        return 403, "forbidden"
    if not verify_twilio_signature(url, params, signature, secrets.get("twilio_auth_token", "")):
        return 403, "forbidden"

    # 3. dedup on MessageSid (Twilio retries on timeouts)
    sid = params.get("MessageSid") or params.get("SmsMessageSid")
    if not sid:
        return 400, "missing MessageSid"
    expires = int((ctx.clock() + MSGSID_TTL).timestamp())
    pk, sk = keys.message_sid(ctx.tenant, sid)
    if not ctx.store.put_if_absent({"pk": pk, "sk": sk, "expires_at": expires}):
        return 200, twiml(None)

    # 4. allowlist
    supplier = find_by_phone(ctx, params.get("From", ""))
    if not supplier or not supplier.get("active"):
        return 200, twiml(reply("en", "not_registered"))

    text = params.get("Body", "")
    media = [params[f"MediaUrl{i}"] for i in range(int(params.get("NumMedia", "0") or 0)) if params.get(f"MediaUrl{i}")]
    message = _route(ctx, supplier, text, media, llm)
    return 200, twiml(message)


def _route(ctx: Ctx, supplier: dict, text: str, media: list[str], llm: LlmFallback | None) -> str:
    lang = supplier.get("language", "en")
    parsed = parse(text, has_media=bool(media), llm=llm)
    log.info("whatsapp %s intent=%s sku=%s qty=%s src=%s", supplier["id"], parsed.intent, parsed.sku,
             parsed.qty, parsed.source)

    if parsed.intent == "confirm":
        return _confirm(ctx, supplier)
    if parsed.intent == "cancel":
        ctx.store.delete(*keys.pending(ctx.tenant, supplier["phone"]))
        return reply(lang, "cancelled")
    if parsed.intent == "help":
        return reply(lang, "help")
    if parsed.intent == "new_product":
        return _new_product(ctx, supplier, text, media)
    if parsed.intent == "unknown" or not parsed.sku:
        return reply(lang, "unknown")

    product = find_product(ctx, parsed.sku)
    if not product:
        return reply(lang, "not_found", sku=parsed.sku, suggest=_suggest(ctx, supplier, lang))
    if product.get("supplier_id") != supplier["id"]:
        return reply(lang, "not_yours", sku=parsed.sku)
    label = _label(product)

    if parsed.intent == "query":
        if product.get("made_to_order"):
            return reply(lang, "stock_mto", sku=parsed.sku, label=label, days=product.get("lead_time_days", 0))
        return reply(lang, "stock", sku=parsed.sku, label=label, on_hand=balance(ctx, parsed.sku))

    if parsed.qty is None or (parsed.qty <= 0 and parsed.intent != "set"):
        return reply(lang, "ask_qty", sku=parsed.sku)
    if parsed.intent == "sold" and not product.get("made_to_order"):
        on_hand = balance(ctx, parsed.sku)
        if parsed.qty > on_hand:
            return reply(lang, "insufficient", sku=parsed.sku, on_hand=on_hand)

    pending_id = f"wa_{ctx.ids()}"
    pk, sk = keys.pending(ctx.tenant, supplier["phone"])
    ctx.store.put({
        "pk": pk, "sk": sk, "id": pending_id, "intent": parsed.intent, "sku": parsed.sku, "qty": parsed.qty,
        "supplier_id": supplier["id"], "expires_at_iso": (ctx.clock() + PENDING_TTL).isoformat(),
        "expires_at": int((ctx.clock() + PENDING_TTL).timestamp()), "source": parsed.source,
    })
    return reply(lang, f"confirm_{parsed.intent}", qty=parsed.qty, sku=parsed.sku, label=label)


def _confirm(ctx: Ctx, supplier: dict) -> str:
    lang = supplier.get("language", "en")
    key = keys.pending(ctx.tenant, supplier["phone"])
    pending = ctx.store.get(*key)
    if not pending:
        return reply(lang, "nothing_pending")
    ctx.store.delete(*key)
    if ctx.clock() > datetime.fromisoformat(pending["expires_at_iso"]):
        return reply(lang, "expired")
    sku, qty, actor = pending["sku"], int(pending["qty"]), f"supplier:{supplier['id']}"
    try:
        if pending["intent"] == "add":
            result = record_change(ctx, sku, qty, "RECEIVE", "whatsapp", actor, pending["id"])
        elif pending["intent"] == "sold":
            result = record_change(ctx, sku, -qty, "SALE", "whatsapp", actor, pending["id"])
        else:
            result = set_count(ctx, sku, qty, "whatsapp", actor, pending["id"])
    except InsufficientStock as e:
        return reply(lang, "insufficient", sku=sku, on_hand=e.on_hand)
    if result.get("production_order_id"):
        return reply(lang, "done_mto", sku=sku, po=result["production_order_id"])
    return reply(lang, "done", sku=sku, on_hand=result["on_hand"])


def _new_product(ctx: Ctx, supplier: dict, text: str, media: list[str]) -> str:
    title = text.strip()[:120] or None
    product = create_product(ctx, {"title": title, "supplier_id": supplier["id"], "craft": supplier.get("craft", "")},
                             actor=f"supplier:{supplier['id']}")
    job = {"type": "ingest_media", "tenant": ctx.tenant, "sku": product["sku"], "media_urls": media}
    (ctx.intelligence_queue or ctx.queue).send(job)
    return reply(supplier.get("language", "en"), "draft_created", sku=product["sku"])


def _label(product: dict) -> str:
    attrs = product.get("attributes") or {}
    parts = [attrs.get("fabric"), attrs.get("primary_color")]
    label = ", ".join(p for p in parts if p)
    return label or product.get("title", product["sku"])[:40]


def _suggest(ctx: Ctx, supplier: dict, lang: str) -> str:
    codes = [p["sku"] for p in list_products(ctx, supplier_id=supplier["id"])][:5]
    return reply(lang, "suggest", codes=", ".join(codes)) if codes else ""
