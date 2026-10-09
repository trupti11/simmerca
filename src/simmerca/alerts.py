"""Operational alerts surfaced in the admin dashboard (oversells, unmapped SKUs, sync failures)."""

from __future__ import annotations

import logging

from . import keys
from .context import Ctx

log = logging.getLogger(__name__)

KINDS = {"OVERSELL", "UNMAPPED_SKU", "SYNC_FAILED", "LOW_CONFIDENCE", "PRICE_BELOW_FLOOR"}


def raise_alert(ctx: Ctx, kind: str, message: str, data: dict | None = None, dedupe_key: str | None = None) -> dict:
    """Record an alert. With dedupe_key, the same alert is recorded once."""
    if kind not in KINDS:
        raise ValueError(f"unknown alert kind {kind}")
    alert_id = dedupe_key or ctx.ids()
    item = {
        "pk": keys.kind_pk(ctx.tenant, "ALERT"),
        "sk": f"{alert_id}",
        "id": alert_id,
        "kind": kind,
        "message": message,
        "data": data or {},
        "status": "open",
        "at": ctx.now_iso(),
    }
    created = ctx.store.put_if_absent(item)
    if created:
        log.warning("alert %s: %s", kind, message)
    return item


def list_alerts(ctx: Ctx, status: str | None = "open") -> list[dict]:
    rows = ctx.store.query(keys.kind_pk(ctx.tenant, "ALERT"))
    rows = [r for r in rows if status is None or r.get("status") == status]
    return sorted(rows, key=lambda r: r["at"], reverse=True)


def resolve_alert(ctx: Ctx, alert_id: str) -> dict:
    pk = keys.kind_pk(ctx.tenant, "ALERT")
    item = ctx.store.get(pk, alert_id)
    if not item:
        from .errors import NotFound

        raise NotFound(f"alert {alert_id} not found")
    item["status"] = "resolved"
    item["resolved_at"] = ctx.now_iso()
    ctx.store.put(item)
    return item
