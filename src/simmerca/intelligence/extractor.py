"""Product intelligence: photo -> proposed attributes with confidence (spec 03).

AI proposes; a human approves. Nothing here ever sets `attributes` directly except approve_attributes.
"""

from __future__ import annotations

import json
import logging
from typing import Callable, Protocol

from ..context import Ctx
from ..errors import Conflict, ValidationError
from ..products import get_product, list_products
from ..vocab import BORDERS, COLORS, FABRIC_PROFILES, MOTIFS, WEAVES, normalize_color, normalize_fabric

log = logging.getLogger(__name__)
REVIEW_THRESHOLD = 0.80
FIELDS = ("fabric", "weave", "primary_color", "secondary_colors", "motif", "border", "zari")


class VisionModel(Protocol):
    def describe(self, images: list[tuple[bytes, str]], prompt: str) -> str:
        """Return the model's text output (expected: one JSON object)."""
        ...


PROMPT = f"""You are a textile expert cataloguing Indian handloom sarees for a weaver-direct brand.
Look at the photo(s) and return ONLY one JSON object, no prose:
{{
 "fabric": {{"value": one of {sorted(FABRIC_PROFILES)} or null, "confidence": 0..1}},
 "weave": {{"value": one of {sorted(WEAVES)}, "confidence": 0..1}},
 "primary_color": {{"value": one of {sorted(COLORS)}, "confidence": 0..1}},
 "secondary_colors": {{"value": [colors from the same list], "confidence": 0..1}},
 "motif": {{"value": one of {sorted(MOTIFS)}, "confidence": 0..1}},
 "border": {{"value": one of {sorted(BORDERS)}, "confidence": 0..1}},
 "zari": {{"value": true or false, "confidence": 0..1}}
}}
Confidence must reflect real uncertainty. Fabric is hard to judge from photos: if unsure, say so with low
confidence rather than guessing. Never invent a value outside the lists."""


def _field(value, confidence) -> dict:
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    if value is None:
        conf = 0.0
    return {"value": value, "confidence": round(conf, 3)}


def normalize_output(raw: str) -> dict:
    """Parse and validate model output. Never raises: bad fields become null with confidence 0."""
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        data = json.loads(raw[start:end])
        if not isinstance(data, dict):
            raise ValueError
    except (ValueError, json.JSONDecodeError):
        data = {}

    def get(name):
        entry = data.get(name)
        if isinstance(entry, dict):
            return entry.get("value"), entry.get("confidence", 0)
        return entry, 0.5 if entry is not None else 0

    out: dict[str, dict] = {}
    v, c = get("fabric")
    out["fabric"] = _field(normalize_fabric(v) if isinstance(v, str) else None, c)

    for name, vocab in (("weave", WEAVES), ("motif", MOTIFS), ("border", BORDERS)):
        v, c = get(name)
        val = str(v).strip().lower() if isinstance(v, str) else None
        out[name] = _field(val if val in vocab else None, c)

    v, c = get("primary_color")
    out["primary_color"] = _field(normalize_color(v) if isinstance(v, str) else None, c)

    v, c = get("secondary_colors")
    colors = []
    if isinstance(v, list):
        for item in v:
            n = normalize_color(item) if isinstance(item, str) else None
            if n and n not in colors and n != out["primary_color"]["value"]:
                colors.append(n)
    out["secondary_colors"] = _field(colors if isinstance(v, list) else None, c)

    v, c = get("zari")
    out["zari"] = _field(v if isinstance(v, bool) else None, c)
    return out


def needs_review(proposal: dict) -> list[str]:
    return [f for f in FIELDS if proposal.get(f, {}).get("confidence", 0) < REVIEW_THRESHOLD]


def propose_attributes(ctx: Ctx, sku: str, model: VisionModel, image_keys: list[str] | None = None) -> dict:
    product = get_product(ctx, sku)
    keys_ = image_keys or product.get("images", [])
    if not keys_:
        raise ValidationError(f"{sku} has no images to analyse")
    images = [(ctx.blobs.get_bytes(k), _media_type(k)) for k in keys_[:4]]
    try:
        raw = model.describe(images, PROMPT)
    except Exception as e:  # noqa: BLE001
        log.exception("vision model failed for %s", sku)
        raw = ""
        product["intelligence_error"] = str(e)[:300]
    proposal = normalize_output(raw)
    product["proposed_attributes"] = proposal
    product["needs_review_fields"] = needs_review(proposal)
    product["attribute_status"] = "pending_review"
    product["proposed_at"] = ctx.now_iso()
    product["updated_at"] = ctx.now_iso()
    ctx.store.put(product)
    return product


def approve_attributes(ctx: Ctx, sku: str, actor: str, edits: dict | None = None) -> dict:
    """Human approval. `edits` override proposed values and are validated against the vocabularies."""
    product = get_product(ctx, sku)
    if product.get("attribute_status") != "pending_review":
        raise Conflict(f"{sku} has no attributes awaiting review")
    proposal = product.get("proposed_attributes") or {}
    final = {f: proposal.get(f, {}).get("value") for f in FIELDS}
    for k, v in (edits or {}).items():
        if k not in FIELDS:
            raise ValidationError(f"unknown attribute {k}")
        final[k] = _validate_edit(k, v)
    product["attributes"] = {k: v for k, v in final.items() if v not in (None, [], "")}
    product["attribute_status"] = "approved"
    product["needs_review_fields"] = []
    product["attributes_approved_by"] = actor
    product["attributes_approved_at"] = ctx.now_iso()
    product["updated_at"] = ctx.now_iso()
    ctx.store.put(product)
    return product


def _validate_edit(field: str, value):
    if value is None:
        return None
    if field == "fabric":
        norm = normalize_fabric(value)
        if norm is None:
            raise ValidationError(f"fabric must be one of {sorted(FABRIC_PROFILES)}")
        return norm
    if field == "primary_color":
        norm = normalize_color(value)
        if norm is None:
            raise ValidationError("unknown color")
        return norm
    if field == "secondary_colors":
        if not isinstance(value, list):
            raise ValidationError("secondary_colors must be a list")
        out = [normalize_color(c) for c in value]
        if None in out:
            raise ValidationError("unknown color in secondary_colors")
        return out
    if field == "zari":
        if not isinstance(value, bool):
            raise ValidationError("zari must be true or false")
        return value
    vocab = {"weave": WEAVES, "motif": MOTIFS, "border": BORDERS}[field]
    if str(value).lower() not in vocab:
        raise ValidationError(f"{field} must be one of {sorted(vocab)}")
    return str(value).lower()


def review_queue(ctx: Ctx) -> list[dict]:
    """Products awaiting review; low-confidence first."""
    rows = [p for p in list_products(ctx) if p.get("attribute_status") == "pending_review"]
    return sorted(rows, key=lambda p: (-len(p.get("needs_review_fields", [])), p.get("proposed_at", "")))


def ingest_media(ctx: Ctx, sku: str, media_urls: list[str], fetch: Callable[[str], tuple[bytes, str]],
                 model: VisionModel | None) -> dict:
    """Copy supplier photos (e.g. Twilio media) into the private bucket, then propose attributes."""
    product = get_product(ctx, sku)
    keys_ = list(product.get("images", []))
    for url in media_urls[:4]:
        data, content_type = fetch(url)
        ext = {"image/png": "png", "image/webp": "webp"}.get(content_type, "jpg")
        key = f"{ctx.tenant}/products/{sku}/{len(keys_) + 1}.{ext}"
        ctx.blobs.put_bytes(key, data, content_type)
        keys_.append(key)
    product["images"] = keys_
    ctx.store.put(product)
    if model is None:
        return product
    return propose_attributes(ctx, sku, model, keys_)


def _media_type(key: str) -> str:
    if key.endswith(".png"):
        return "image/png"
    if key.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"
