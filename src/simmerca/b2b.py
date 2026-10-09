"""B2B buyers and quotes (spec 06)."""

from __future__ import annotations

import logging

from . import keys
from .context import Ctx
from .errors import Conflict, Forbidden, InsufficientStock, NotFound, ValidationError
from .ledger import record_change
from .pricing import floor_price, unit_price
from .products import availability, available_qty, find_product

log = logging.getLogger(__name__)
MAX_LINES = 50
MAX_QTY = 500


def _buyer_key(ctx: Ctx, buyer_id: str) -> tuple[str, str]:
    return keys.kind_pk(ctx.tenant, "BUYER"), buyer_id


def upsert_buyer_profile(ctx: Ctx, buyer_id: str, data: dict) -> dict:
    """Buyer creates or edits their own profile. Status stays as set by admin."""
    company = (data.get("company") or "").strip()
    if not company:
        raise ValidationError("company is required")
    existing = ctx.store.get(*_buyer_key(ctx, buyer_id)) or {}
    item = {
        "pk": _buyer_key(ctx, buyer_id)[0], "sk": buyer_id, "id": buyer_id,
        "company": company,
        "contact_name": data.get("contact_name", ""),
        "email": data.get("email", ""),
        "country": data.get("country", ""),
        "status": existing.get("status", "pending"),
        "tier": existing.get("tier", "wholesale"),
        "created_at": existing.get("created_at", ctx.now_iso()),
        "updated_at": ctx.now_iso(),
    }
    ctx.store.put(item)
    return item


def get_buyer(ctx: Ctx, buyer_id: str) -> dict:
    item = ctx.store.get(*_buyer_key(ctx, buyer_id))
    if not item:
        raise NotFound("buyer profile not found")
    return item


def list_buyers(ctx: Ctx, status: str | None = None) -> list[dict]:
    rows = ctx.store.query(keys.kind_pk(ctx.tenant, "BUYER"))
    return [r for r in rows if status is None or r.get("status") == status]


def set_buyer_status(ctx: Ctx, buyer_id: str, status: str, actor: str) -> dict:
    if status not in {"approved", "rejected", "pending", "suspended"}:
        raise ValidationError("invalid buyer status")
    buyer = get_buyer(ctx, buyer_id)
    buyer.update(status=status, status_by=actor, status_at=ctx.now_iso())
    ctx.store.put(buyer)
    return buyer


def create_quote(ctx: Ctx, buyer_id: str, lines: list[dict], note: str = "") -> dict:
    buyer = get_buyer(ctx, buyer_id)
    if buyer.get("status") != "approved":
        raise Forbidden("buyer account is not approved yet")
    if not lines or len(lines) > MAX_LINES:
        raise ValidationError(f"a quote needs 1-{MAX_LINES} lines")
    priced, currency, total = [], None, 0
    for i, line in enumerate(lines):
        sku, qty = line.get("sku"), line.get("qty")
        if not isinstance(qty, int) or isinstance(qty, bool) or not 1 <= qty <= MAX_QTY:
            raise ValidationError(f"line {i}: qty must be an integer 1-{MAX_QTY}")
        product = find_product(ctx, sku or "")
        if not product or product.get("status") != "active":
            raise ValidationError(f"line {i}: {sku} is not available")
        if currency and product.get("currency") != currency:
            raise ValidationError("all lines must share one currency")
        currency = product.get("currency", "USD")
        p = unit_price(ctx, product, qty, b2b=True)
        line_total = p["unit_price_cents"] * qty
        total += line_total
        priced.append({
            "sku": sku, "title": product.get("title", ""), "qty": qty,
            "unit_price_cents": p["unit_price_cents"], "discount_pct": p["discount_pct"],
            "line_total_cents": line_total, "availability": availability(ctx, product),
        })
    qid = f"q_{ctx.ids()}"
    quote = {
        "pk": keys.kind_pk(ctx.tenant, "QUOTE"), "sk": qid, "id": qid, "buyer_id": buyer_id,
        "lines": priced, "currency": currency, "total_cents": total, "status": "requested",
        "buyer_note": note, "created_at": ctx.now_iso(),
    }
    ctx.store.put(quote)
    return quote


def get_quote(ctx: Ctx, quote_id: str, buyer_id: str | None = None) -> dict:
    quote = ctx.store.get(keys.kind_pk(ctx.tenant, "QUOTE"), quote_id)
    if not quote or (buyer_id is not None and quote["buyer_id"] != buyer_id):
        raise NotFound("quote not found")  # same answer for "not yours" to avoid leaking ids
    return quote


def list_quotes(ctx: Ctx, buyer_id: str | None = None, status: str | None = None) -> list[dict]:
    rows = ctx.store.query(keys.kind_pk(ctx.tenant, "QUOTE"))
    if buyer_id is not None:
        rows = [r for r in rows if r["buyer_id"] == buyer_id]
    if status:
        rows = [r for r in rows if r["status"] == status]
    return sorted(rows, key=lambda r: r["created_at"], reverse=True)


def approve_quote(ctx: Ctx, quote_id: str, actor: str, price_overrides: dict | None = None,
                  message: str = "") -> dict:
    """Approve, optionally overriding unit prices (never below floor), and reserve stock."""
    quote = get_quote(ctx, quote_id)
    if quote["status"] != "requested":
        raise Conflict(f"quote is already {quote['status']}")
    overrides = price_overrides or {}

    # 1) validate everything before writing anything (quantities summed per sku)
    products: dict[str, dict] = {}
    wanted: dict[str, int] = {}
    for line in quote["lines"]:
        product = find_product(ctx, line["sku"])
        if not product:
            raise ValidationError(f"{line['sku']} no longer exists")
        products[line["sku"]] = product
        wanted[line["sku"]] = wanted.get(line["sku"], 0) + int(line["qty"])
        if line["sku"] in overrides:
            price = overrides[line["sku"]]
            floor = floor_price(int(product.get("cost_cents", 0)), int(ctx.settings.get("min_margin_pct", 25)))
            if not isinstance(price, int) or price < floor:
                raise ValidationError(f"{line['sku']}: override below floor {floor}")
    for sku, qty in wanted.items():
        if not products[sku].get("made_to_order") and available_qty(ctx, products[sku]) < qty:
            raise InsufficientStock(sku, available_qty(ctx, products[sku]), qty)

    # 2) reserve. Each approval attempt has its own idempotency keys, so a rolled-back attempt never
    #    blocks a later one. In-stock lines go first (they can fail); made-to-order lines last.
    attempt = int(quote.get("approval_attempts", 0)) + 1
    quote["approval_attempts"] = attempt
    ctx.store.put(quote)
    order = sorted(enumerate(quote["lines"]), key=lambda il: bool(products[il[1]["sku"]].get("made_to_order")))
    applied: list[tuple[int, dict, dict]] = []
    try:
        for i, line in order:
            result = record_change(ctx, line["sku"], -line["qty"], "SALE", "b2b", actor,
                                   f"quote:{quote_id}:a{attempt}:{i}", ref=quote_id)
            applied.append((i, line, result))
    except Exception:
        for i, line, result in applied:
            if result.get("production_order_id"):
                _cancel_production_order(ctx, result["production_order_id"])
            elif result.get("applied"):
                record_change(ctx, line["sku"], line["qty"], "RETURN", "b2b", actor,
                              f"quote:{quote_id}:a{attempt}:{i}:rollback", ref=quote_id)
        raise

    total = 0
    for line in quote["lines"]:
        if line["sku"] in overrides:
            line["unit_price_cents"] = overrides[line["sku"]]
            line["line_total_cents"] = line["unit_price_cents"] * line["qty"]
        total += line["line_total_cents"]
    quote.update(status="approved", total_cents=total, decided_by=actor, decided_at=ctx.now_iso(),
                 message_to_buyer=message)
    ctx.store.put(quote)
    return quote


def _cancel_production_order(ctx: Ctx, po_id: str) -> None:
    pk = keys.kind_pk(ctx.tenant, "PRODORDER")
    po = ctx.store.get(pk, po_id)
    if po:
        po.update(status="cancelled", cancelled_at=ctx.now_iso())
        ctx.store.put(po)


def reject_quote(ctx: Ctx, quote_id: str, actor: str, message: str = "") -> dict:
    quote = get_quote(ctx, quote_id)
    if quote["status"] != "requested":
        raise Conflict(f"quote is already {quote['status']}")
    quote.update(status="rejected", decided_by=actor, decided_at=ctx.now_iso(), message_to_buyer=message)
    ctx.store.put(quote)
    return quote
