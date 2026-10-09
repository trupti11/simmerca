"""Pricing rules, suggestions and approval-gated price changes (spec 05). Integer cents only."""

from __future__ import annotations

from statistics import median

from . import keys
from .context import Ctx
from .errors import Conflict, NotFound, ValidationError
from .products import get_product, list_products

DEFAULT_MIN_MARGIN_PCT = 25
DEFAULT_CHANNEL_FEE_PCT = 10
DEFAULT_TIERS: list[tuple[int, int]] = [(1, 0), (5, 10), (10, 18)]  # (min_qty, discount_pct)


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def round_up_unit(cents: int) -> int:
    """Round up to a whole currency unit (e.g. 12345 -> 12400)."""
    return _ceil_div(cents, 100) * 100


def rule_price(cost_cents: int, margin_pct: int, fee_pct: int = DEFAULT_CHANNEL_FEE_PCT) -> int:
    """cost x (1 + margin) / (1 - fee), rounded up to a whole unit."""
    if cost_cents < 0 or margin_pct < 0 or not 0 <= fee_pct < 100:
        raise ValidationError("invalid pricing inputs")
    raw = _ceil_div(cost_cents * (100 + margin_pct) * 100, 100 * (100 - fee_pct))
    return round_up_unit(raw)


def floor_price(cost_cents: int, min_margin_pct: int = DEFAULT_MIN_MARGIN_PCT) -> int:
    return _ceil_div(cost_cents * (100 + min_margin_pct), 100)


def tier_discount_pct(qty: int, tiers: list[tuple[int, int]] | None = None) -> int:
    pct = 0
    for min_qty, discount in sorted(tiers or DEFAULT_TIERS):
        if qty >= min_qty:
            pct = discount
    return pct


def unit_price(ctx: Ctx, product: dict, qty: int, b2b: bool = False) -> dict:
    """Unit price for a quantity. Retail buyers pay list price; B2B gets tier discounts, never below floor."""
    retail = int(product["price_cents"])
    floor = floor_price(int(product.get("cost_cents", 0)), _min_margin(ctx))
    discount = tier_discount_pct(qty, _tiers(ctx)) if b2b else 0
    price = retail - (retail * discount) // 100
    clamped = price < floor
    return {
        "unit_price_cents": max(price, floor),
        "retail_price_cents": retail,
        "discount_pct": discount,
        "clamped_to_floor": clamped,
        "currency": product.get("currency", "USD"),
    }


def tier_table(ctx: Ctx, product: dict) -> list[dict]:
    out = []
    for min_qty, _ in sorted(_tiers(ctx)):
        p = unit_price(ctx, product, min_qty, b2b=True)
        out.append({"min_qty": min_qty, "unit_price_cents": p["unit_price_cents"], "discount_pct": p["discount_pct"]})
    return out


def _tiers(ctx: Ctx) -> list[tuple[int, int]]:
    return [tuple(t) for t in ctx.settings.get("tiers", DEFAULT_TIERS)]


def _min_margin(ctx: Ctx) -> int:
    return int(ctx.settings.get("min_margin_pct", DEFAULT_MIN_MARGIN_PCT))


def _fee(ctx: Ctx) -> int:
    return int(ctx.settings.get("channel_fee_pct", DEFAULT_CHANNEL_FEE_PCT))


# ---------- comparables & suggestions ----------

def add_comparable(ctx: Ctx, data: dict, actor: str) -> dict:
    fabric = data.get("fabric")
    price = data.get("price_cents")
    if not fabric or not isinstance(price, int) or price <= 0:
        raise ValidationError("fabric and a positive integer price_cents are required")
    cid = f"cmp_{ctx.ids()}"
    item = {
        "pk": keys.kind_pk(ctx.tenant, "COMPARABLE"), "sk": cid, "id": cid,
        "fabric": fabric, "price_cents": price, "currency": data.get("currency", "USD"),
        "note": data.get("note", ""), "added_by": actor, "at": ctx.now_iso(),
    }
    ctx.store.put(item)
    return item


def suggest_price(ctx: Ctx, sku: str) -> dict:
    """Median of comparables (same fabric, same currency), clamped to [floor, 2 x rule price]."""
    product = get_product(ctx, sku)
    fabric = (product.get("attributes") or {}).get("fabric")
    currency = product.get("currency", "USD")
    cost = int(product.get("cost_cents", 0))
    rule = rule_price(cost, int(product.get("margin_pct", 60)), _fee(ctx))
    floor = floor_price(cost, _min_margin(ctx))

    own = [
        int(p["price_cents"]) for p in list_products(ctx, status="active")
        if p["sku"] != sku and p.get("currency") == currency and p.get("price_cents", 0) > 0
        and (p.get("attributes") or {}).get("fabric") == fabric
    ]
    external = [
        int(c["price_cents"]) for c in ctx.store.query(keys.kind_pk(ctx.tenant, "COMPARABLE"))
        if c.get("fabric") == fabric and c.get("currency") == currency
    ]
    comps = own + external
    if comps:
        basis = int(median(comps))
        method = "median_of_comparables"
    else:
        basis = rule
        method = "rule_price_no_comparables"
    suggested = round_up_unit(min(max(basis, floor), 2 * rule))
    return {
        "sku": sku,
        "suggested_price_cents": suggested,
        "rule_price_cents": rule,
        "floor_price_cents": floor,
        "current_price_cents": int(product.get("price_cents", 0)),
        "currency": currency,
        "method": method,
        "comparables": {"own_catalog": len(own), "owner_entered": len(external), "fabric": fabric},
    }


# ---------- approval-gated price changes ----------

def propose_price(ctx: Ctx, sku: str, price_cents: int, actor: str, reason: str = "") -> dict:
    product = get_product(ctx, sku)
    if not isinstance(price_cents, int) or price_cents <= 0:
        raise ValidationError("price_cents must be a positive integer")
    floor = floor_price(int(product.get("cost_cents", 0)), _min_margin(ctx))
    if price_cents < floor:
        raise ValidationError(f"price {price_cents} is below the floor {floor}")
    pid = f"pp_{ctx.ids()}"
    item = {
        "pk": keys.kind_pk(ctx.tenant, "PRICEPROP"), "sk": pid, "id": pid, "sku": sku,
        "old_price_cents": int(product.get("price_cents", 0)), "new_price_cents": price_cents,
        "currency": product.get("currency", "USD"), "reason": reason, "status": "pending",
        "proposed_by": actor, "at": ctx.now_iso(),
    }
    ctx.store.put(item)
    return item


def list_proposals(ctx: Ctx, status: str | None = "pending") -> list[dict]:
    rows = ctx.store.query(keys.kind_pk(ctx.tenant, "PRICEPROP"))
    return [r for r in rows if status is None or r["status"] == status]


def _get_proposal(ctx: Ctx, pid: str) -> dict:
    item = ctx.store.get(keys.kind_pk(ctx.tenant, "PRICEPROP"), pid)
    if not item:
        raise NotFound(f"price proposal {pid} not found")
    if item["status"] != "pending":
        raise Conflict(f"proposal is already {item['status']}")
    return item


def approve_proposal(ctx: Ctx, pid: str, actor: str) -> dict:
    prop = _get_proposal(ctx, pid)
    product = get_product(ctx, prop["sku"])
    product["price_cents"] = prop["new_price_cents"]
    product["updated_at"] = ctx.now_iso()
    product["updated_by"] = actor
    ctx.store.put(product)
    prop.update(status="approved", decided_by=actor, decided_at=ctx.now_iso())
    ctx.store.put(prop)
    ctx.queue.send({"type": "push_price", "tenant": ctx.tenant, "sku": prop["sku"]})
    return prop


def reject_proposal(ctx: Ctx, pid: str, actor: str) -> dict:
    prop = _get_proposal(ctx, pid)
    prop.update(status="rejected", decided_by=actor, decided_at=ctx.now_iso())
    ctx.store.put(prop)
    return prop
