"""Product catalog. One product = one sellable piece type (a handloom saree is often quantity 1)."""

from __future__ import annotations

from . import keys
from .context import Ctx
from .errors import NotFound, ValidationError
from .vocab import FABRIC_PROFILES, sku_prefix

STATUSES = {"draft", "active", "archived"}
ATTRIBUTE_FIELDS = ("fabric", "weave", "primary_color", "secondary_colors", "motif", "border", "zari")

# Fields an admin may PATCH directly. Price is NOT here once active: use price proposals (spec 05).
PATCHABLE = {
    "title", "description", "story_public", "provenance_region", "craft", "images", "supplier_id",
    "cost_cents", "currency", "made_to_order", "lead_time_days", "mto_capacity", "margin_pct",
    "tags", "internal_notes", "weight_grams", "length_m",
}


def _int(value, name: str, minimum: int = 0) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{name} must be an integer") from None
    if v < minimum:
        raise ValidationError(f"{name} must be >= {minimum}")
    return v


def next_sku(ctx: Ctx, fabric: str | None) -> str:
    prefix = sku_prefix(fabric)
    n = ctx.store.increment(*keys.counter(ctx.tenant, f"sku:{prefix}"))
    return f"{prefix}-{n:04d}"


def create_product(ctx: Ctx, data: dict, actor: str) -> dict:
    """Create a draft product. Attributes start empty; intelligence proposes them later."""
    fabric = data.get("fabric")
    if fabric and fabric not in FABRIC_PROFILES:
        raise ValidationError(f"fabric must be one of {sorted(FABRIC_PROFILES)}")
    sku = data.get("sku") or next_sku(ctx, fabric)
    now = ctx.now_iso()
    attributes = {"fabric": fabric} if fabric else {}
    product = {
        "pk": keys.product(ctx.tenant, sku)[0],
        "sk": sku,
        "sku": sku,
        "title": (data.get("title") or f"Handloom piece {sku}").strip(),
        "description": data.get("description", ""),
        "story_public": data.get("story_public", ""),
        "provenance_region": data.get("provenance_region", ""),
        "craft": data.get("craft", ""),
        "supplier_id": data.get("supplier_id"),
        "cost_cents": _int(data.get("cost_cents", 0), "cost_cents"),
        "price_cents": _int(data.get("price_cents", 0), "price_cents"),
        "currency": data.get("currency", "USD"),
        "margin_pct": _int(data.get("margin_pct", 60), "margin_pct"),
        "images": list(data.get("images", [])),
        "attributes": attributes,
        "attribute_status": "approved" if attributes else "none",
        "proposed_attributes": {},
        "needs_review_fields": [],
        "made_to_order": bool(data.get("made_to_order", False)),
        "lead_time_days": _int(data.get("lead_time_days", 0), "lead_time_days"),
        "mto_capacity": _int(data.get("mto_capacity", 0), "mto_capacity"),
        "status": "draft",
        "channels": [],
        "tags": list(data.get("tags", [])),
        "internal_notes": data.get("internal_notes", ""),
        "created_at": now,
        "updated_at": now,
        "created_by": actor,
    }
    if not ctx.store.put_if_absent(product):
        raise ValidationError(f"sku {sku} already exists")
    return product


def get_product(ctx: Ctx, sku: str) -> dict:
    item = ctx.store.get(*keys.product(ctx.tenant, sku)) if sku else None
    if not item:
        raise NotFound(f"product {sku} not found")
    return item


def find_product(ctx: Ctx, sku: str | None) -> dict | None:
    if not sku or not isinstance(sku, str):
        return None
    return ctx.store.get(*keys.product(ctx.tenant, sku))


def list_products(ctx: Ctx, status: str | None = None, supplier_id: str | None = None) -> list[dict]:
    rows = ctx.store.query(keys.kind_pk(ctx.tenant, "PRODUCT"))
    if status:
        rows = [r for r in rows if r.get("status") == status]
    if supplier_id:
        rows = [r for r in rows if r.get("supplier_id") == supplier_id]
    return rows


def update_product(ctx: Ctx, sku: str, patch: dict, actor: str) -> dict:
    product = get_product(ctx, sku)
    unknown = set(patch) - PATCHABLE - {"price_cents", "status"}
    if unknown:
        raise ValidationError(f"fields not editable: {sorted(unknown)}")
    if "price_cents" in patch:
        if product["status"] != "draft":
            raise ValidationError("price of a non-draft product changes only through a price proposal")
        product["price_cents"] = _int(patch["price_cents"], "price_cents")
    for field in ("cost_cents", "lead_time_days", "mto_capacity", "margin_pct"):
        if field in patch:
            patch[field] = _int(patch[field], field)
    for k, v in patch.items():
        if k in PATCHABLE:
            product[k] = v
    if "status" in patch:
        _apply_status(product, patch["status"])
    product["updated_at"] = ctx.now_iso()
    product["updated_by"] = actor
    ctx.store.put(product)
    if {"status", "made_to_order", "mto_capacity"} & set(patch):
        ctx.queue.send({"type": "push_stock", "tenant": ctx.tenant, "sku": sku})
    return product


def _apply_status(product: dict, status: str) -> None:
    if status not in STATUSES:
        raise ValidationError(f"status must be one of {sorted(STATUSES)}")
    if status == "active":
        if product.get("price_cents", 0) <= 0:
            raise ValidationError("set a price before activating")
        if product.get("attribute_status") == "pending_review":
            raise ValidationError("approve the proposed attributes before activating")
    product["status"] = status


def available_qty(ctx: Ctx, product: dict) -> int:
    """Quantity a channel may sell right now."""
    if product.get("status") != "active":
        return 0
    if product.get("made_to_order"):
        return int(product.get("mto_capacity", 0))
    bal = ctx.store.get(*keys.balance(ctx.tenant, product["sku"]))
    return int(bal["on_hand"]) if bal else 0


def availability(ctx: Ctx, product: dict) -> dict:
    """Buyer-facing availability. Made-to-order never reads as in stock."""
    if product.get("made_to_order"):
        return {"status": "made_to_order", "quantity": None, "lead_time_days": int(product.get("lead_time_days", 0))}
    qty = available_qty(ctx, product)
    return {"status": "in_stock" if qty > 0 else "out_of_stock", "quantity": qty, "lead_time_days": 0}
