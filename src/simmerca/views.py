"""Buyer-facing projections. WHITELISTS ONLY (spec 07).

Never add supplier_id, supplier fields, cost_cents, proposed attributes or internal notes here.
"""

from __future__ import annotations

from .context import Ctx
from .pricing import tier_table
from .products import availability

PUBLIC_ATTRIBUTE_FIELDS = ("fabric", "weave", "primary_color", "secondary_colors", "motif", "border", "zari")


def _attributes(product: dict) -> dict:
    approved = product.get("attributes") or {}
    return {k: approved[k] for k in PUBLIC_ATTRIBUTE_FIELDS if approved.get(k) not in (None, "", [])}


def public_product(ctx: Ctx, product: dict) -> dict:
    return {
        "sku": product["sku"],
        "title": product.get("title", ""),
        "description": product.get("description", ""),
        "story": product.get("story_public", ""),
        "provenance_region": product.get("provenance_region", ""),
        "craft": product.get("craft", ""),
        "attributes": _attributes(product),
        "images": [ctx.blobs.presign_get(k) for k in product.get("images", [])],
        "price_cents": int(product.get("price_cents", 0)),
        "currency": product.get("currency", "USD"),
        "availability": availability(ctx, product),
        "tags": list(product.get("tags", [])),
    }


def b2b_product(ctx: Ctx, product: dict) -> dict:
    out = public_product(ctx, product)
    out["tier_prices"] = tier_table(ctx, product)
    return out


def quote_view(quote: dict) -> dict:
    return {
        "id": quote["id"],
        "status": quote["status"],
        "currency": quote.get("currency", "USD"),
        "lines": [
            {
                "sku": line["sku"],
                "title": line.get("title", ""),
                "qty": line["qty"],
                "unit_price_cents": line["unit_price_cents"],
                "line_total_cents": line["line_total_cents"],
                "availability": line.get("availability", {}),
            }
            for line in quote.get("lines", [])
        ],
        "total_cents": quote.get("total_cents", 0),
        "message": quote.get("message_to_buyer", ""),
        "created_at": quote.get("created_at"),
        "decided_at": quote.get("decided_at"),
    }


def buyer_view(buyer: dict) -> dict:
    return {k: buyer.get(k) for k in ("id", "company", "contact_name", "email", "country", "status", "tier")}
