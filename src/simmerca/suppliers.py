"""Suppliers (weavers). Internal only: never exposed to buyers."""

from __future__ import annotations

import re

from . import keys
from .context import Ctx
from .errors import Conflict, NotFound, ValidationError

LANGUAGES = {"en", "hi", "mr", "bn", "gu", "ta", "te", "or", "as", "kn"}


def normalize_phone(raw: str) -> str:
    """'whatsapp:+91 98450-12345' -> '+919845012345'."""
    if not raw:
        raise ValidationError("phone is required")
    s = raw.strip()
    if s.lower().startswith("whatsapp:"):
        s = s[len("whatsapp:"):]
    digits = re.sub(r"[^\d+]", "", s)
    if not digits.startswith("+"):
        raise ValidationError("phone must be in E.164 format, e.g. +919845012345")
    if not re.fullmatch(r"\+\d{8,15}", digits):
        raise ValidationError("phone must have 8-15 digits")
    return digits


def create_supplier(ctx: Ctx, data: dict) -> dict:
    name = (data.get("name") or "").strip()
    if not name:
        raise ValidationError("name is required")
    phone = normalize_phone(data.get("phone", ""))
    lang = data.get("language", "en")
    if lang not in LANGUAGES:
        raise ValidationError(f"language must be one of {sorted(LANGUAGES)}")
    supplier_id = data.get("id") or f"sup_{ctx.ids()}"
    pk, sk = keys.supplier_phone(ctx.tenant, phone)
    if not ctx.store.put_if_absent({"pk": pk, "sk": sk, "supplier_id": supplier_id}):
        raise Conflict("a supplier with this phone already exists")
    item = {
        "pk": keys.supplier(ctx.tenant, supplier_id)[0],
        "sk": supplier_id,
        "id": supplier_id,
        "name": name,
        "phone": phone,
        "language": lang,
        "region": data.get("region", ""),
        "craft": data.get("craft", ""),
        "notes": data.get("notes", ""),
        "active": bool(data.get("active", True)),
        "created_at": ctx.now_iso(),
    }
    ctx.store.put(item)
    return item


def get_supplier(ctx: Ctx, supplier_id: str) -> dict:
    item = ctx.store.get(*keys.supplier(ctx.tenant, supplier_id))
    if not item:
        raise NotFound(f"supplier {supplier_id} not found")
    return item


def find_by_phone(ctx: Ctx, phone: str) -> dict | None:
    try:
        phone = normalize_phone(phone)
    except ValidationError:
        return None
    ref = ctx.store.get(*keys.supplier_phone(ctx.tenant, phone))
    if not ref:
        return None
    return ctx.store.get(*keys.supplier(ctx.tenant, ref["supplier_id"]))


def list_suppliers(ctx: Ctx) -> list[dict]:
    return ctx.store.query(keys.kind_pk(ctx.tenant, "SUPPLIER"))


def set_active(ctx: Ctx, supplier_id: str, active: bool) -> dict:
    item = get_supplier(ctx, supplier_id)
    item["active"] = active
    ctx.store.put(item)
    return item
