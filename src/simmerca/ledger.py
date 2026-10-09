"""Stock ledger: the single source of inventory truth (spec 01)."""

from __future__ import annotations

import logging

from . import keys
from .context import Ctx
from .errors import ConcurrentModification, DuplicateRequest, InsufficientStock, ValidationError
from .products import get_product
from .store import StockFloorViolation

log = logging.getLogger(__name__)

# reason -> required sign of delta (+1, -1, or 0 = either)
REASONS = {"RECEIVE": 1, "RETURN": 1, "SALE": -1, "ADJUST": 0, "SET": 0, "DAMAGE": -1}


def balance(ctx: Ctx, sku: str) -> int:
    item = ctx.store.get(*keys.balance(ctx.tenant, sku))
    return int(item["on_hand"]) if item else 0


def record_change(
    ctx: Ctx,
    sku: str,
    delta: int,
    reason: str,
    source: str,
    actor: str,
    idem_key: str,
    ref: str | None = None,
    expected_on_hand: int | None = None,
) -> dict:
    """Apply one stock change atomically and idempotently.

    Returns {"on_hand", "applied", "event_id"|None, "production_order_id"|None}.
    A repeated idem_key returns the original result with applied=False.
    """
    if reason not in REASONS:
        raise ValidationError(f"reason must be one of {sorted(REASONS)}")
    if not isinstance(delta, int) or isinstance(delta, bool):
        raise ValidationError("delta must be an integer")
    sign = REASONS[reason]
    if (sign > 0 and delta <= 0) or (sign < 0 and delta >= 0):
        raise ValidationError(f"{reason} requires a {'positive' if sign > 0 else 'negative'} delta")
    if not idem_key:
        raise ValidationError("idem_key is required")

    product = get_product(ctx, sku)
    now = ctx.now_iso()
    event_id = ctx.ids()
    extra: list[dict] = []
    production_order_id = None
    applied_delta = delta

    if product.get("made_to_order") and reason == "SALE":
        # Made-to-order: stock is not decremented; a production order is created instead.
        applied_delta = 0
        production_order_id = f"po_{event_id}"
        extra.append({
            "pk": keys.kind_pk(ctx.tenant, "PRODORDER"),
            "sk": production_order_id,
            "id": production_order_id,
            "sku": sku,
            "qty": -delta,
            "supplier_id": product.get("supplier_id"),
            "ref": ref,
            "status": "requested",
            "lead_time_days": product.get("lead_time_days", 0),
            "at": now,
        })

    current = balance(ctx, sku)
    extra.append({
        "pk": keys.ledger_pk(ctx.tenant, sku),
        "sk": f"{now}#{event_id}",
        "id": event_id,
        "sku": sku,
        "delta": applied_delta,
        "requested_delta": delta,
        "reason": reason,
        "source": source,
        "actor": actor,
        "ref": ref,
        "idem_key": idem_key,
        "on_hand_before": current,
        "on_hand_after": current + applied_delta,
        "production_order_id": production_order_id,
        "at": now,
    })

    try:
        new = ctx.store.apply_stock_change(
            keys.balance(ctx.tenant, sku),
            applied_delta,
            keys.idem(ctx.tenant, idem_key),
            extra,
            min_result=0,
            expected_on_hand=expected_on_hand,
        )
    except DuplicateRequest as dup:
        return {"on_hand": int(dup.result.get("on_hand", balance(ctx, sku))), "applied": False,
                "event_id": None, "production_order_id": None}
    except StockFloorViolation as v:
        raise InsufficientStock(sku, v.on_hand, -delta) from None

    ctx.queue.send({"type": "push_stock", "tenant": ctx.tenant, "sku": sku})
    log.info("ledger %s %s %+d -> %d (%s)", sku, reason, applied_delta, new, idem_key)
    return {"on_hand": new, "applied": True, "event_id": event_id, "production_order_id": production_order_id}


def set_count(ctx: Ctx, sku: str, count: int, source: str, actor: str, idem_key: str, retries: int = 3) -> dict:
    """Correct the balance to an absolute physical count (reason SET)."""
    if count < 0:
        raise ValidationError("count must be >= 0")
    for _ in range(retries):
        current = balance(ctx, sku)
        if current == count:
            return {"on_hand": current, "applied": False, "event_id": None, "production_order_id": None}
        try:
            return record_change(ctx, sku, count - current, "SET", source, actor, idem_key, expected_on_hand=current)
        except ConcurrentModification:
            continue
    raise ConcurrentModification(f"stock for {sku} kept changing; try again")


def events(ctx: Ctx, sku: str, limit: int = 100) -> list[dict]:
    return ctx.store.query(keys.ledger_pk(ctx.tenant, sku), reverse=True, limit=limit)


def reconcile(ctx: Ctx, sku: str) -> dict:
    """Compare the stored balance with the sum of ledger events."""
    rows = ctx.store.query(keys.ledger_pk(ctx.tenant, sku))
    total = sum(int(r["delta"]) for r in rows)
    stored = balance(ctx, sku)
    return {"sku": sku, "ledger_sum": total, "balance": stored, "ok": total == stored, "events": len(rows)}
