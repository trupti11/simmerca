"""Channel sync engine (spec 04): listings, outbound pushes, inbound orders, polling."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Mapping

from .. import keys
from ..alerts import raise_alert
from ..context import Ctx
from ..errors import ChannelError, InsufficientStock, NotFound, ValidationError
from ..ledger import record_change
from ..products import available_qty, find_product, get_product
from .base import ChannelAdapter, ChannelOrder

log = logging.getLogger(__name__)
CHANNELS = ("aalora", "shopify", "etsy", "meta")


def link_listing(ctx: Ctx, sku: str, channel: str, adapters: Mapping[str, ChannelAdapter],
                 external: dict | None = None, actor: str = "admin") -> dict:
    if channel not in CHANNELS:
        raise ValidationError(f"channel must be one of {CHANNELS}")
    product = get_product(ctx, sku)
    if external is None:
        adapter = adapters.get(channel)
        if adapter is None:
            raise ValidationError(f"channel {channel} is not configured")
        external = adapter.discover_listing(sku)
        if not external:
            raise NotFound(f"{sku} was not found on {channel}; create the listing there with this sku first")
    pk, sk = keys.listing(ctx.tenant, sku, channel)
    item = {"pk": pk, "sk": sk, "sku": sku, "channel": channel, "external": external,
            "linked_by": actor, "linked_at": ctx.now_iso()}
    ctx.store.put(item)
    ipk, isk = keys.channel_listing(ctx.tenant, channel, sku)
    ctx.store.put({"pk": ipk, "sk": isk, "sku": sku})
    if channel not in product.get("channels", []):
        product["channels"] = sorted(set(product.get("channels", [])) | {channel})
        ctx.store.put(product)
    ctx.queue.send({"type": "push_stock", "tenant": ctx.tenant, "sku": sku})
    ctx.queue.send({"type": "push_price", "tenant": ctx.tenant, "sku": sku})
    return item


def unlink_listing(ctx: Ctx, sku: str, channel: str) -> None:
    ctx.store.delete(*keys.listing(ctx.tenant, sku, channel))
    ctx.store.delete(*keys.channel_listing(ctx.tenant, channel, sku))
    product = get_product(ctx, sku)
    product["channels"] = [c for c in product.get("channels", []) if c != channel]
    ctx.store.put(product)


def listings_for(ctx: Ctx, sku: str) -> list[dict]:
    return ctx.store.query(keys.listings_pk(ctx.tenant, sku))


def skus_on_channel(ctx: Ctx, channel: str) -> list[str]:
    return [r["sku"] for r in ctx.store.query(keys.channel_listing(ctx.tenant, channel, "")[0])]


def process_job(ctx: Ctx, job: dict, adapters: Mapping[str, ChannelAdapter]) -> dict:
    """Handle one SQS job. Raises ChannelError if any channel failed, so SQS retries (pushes are absolute)."""
    kind, sku = job.get("type"), job.get("sku")
    if kind not in {"push_stock", "push_price"}:
        raise ValidationError(f"unknown job type {kind}")
    product = find_product(ctx, sku or "")
    if not product:
        log.warning("job for missing product %s dropped", sku)
        return {"skipped": True}
    results, failures = {}, []
    for listing in listings_for(ctx, sku):
        adapter = adapters.get(listing["channel"])
        if adapter is None:
            continue
        try:
            if kind == "push_stock":
                qty = available_qty(ctx, product)
                adapter.push_inventory(listing, qty)
                listing.update(last_pushed_qty=qty, last_pushed_at=ctx.now_iso(), last_error=None)
            else:
                adapter.push_price(listing, int(product["price_cents"]), product.get("currency", "USD"))
                listing.update(last_pushed_price_cents=int(product["price_cents"]), last_price_at=ctx.now_iso(),
                               last_error=None)
            results[listing["channel"]] = "ok"
        except ChannelError as e:
            listing["last_error"] = str(e)[:300]
            failures.append(f"{listing['channel']}: {e}")
            results[listing["channel"]] = "error"
        ctx.store.put(listing)
    if failures:
        raise ChannelError("; ".join(failures))
    return results


def ingest_order(ctx: Ctx, order: ChannelOrder) -> dict:
    """Each line -> ledger SALE, idempotent per channel line. Never raises for a bad line."""
    summary = {"applied": 0, "duplicate": 0, "unmapped": 0, "oversold": 0}
    for line in order.lines:
        ref = f"{order.channel}:{order.order_id}:{line.line_id}"
        product = find_product(ctx, line.sku) if line.sku else None
        if not product:
            raise_alert(ctx, "UNMAPPED_SKU", f"{order.channel} order {order.order_id} line has unknown sku {line.sku!r}",
                        {"channel": order.channel, "order_id": order.order_id, "line_id": line.line_id,
                         "sku": line.sku, "qty": line.qty}, dedupe_key=f"unmapped:{ref}")
            summary["unmapped"] += 1
            continue
        try:
            result = record_change(ctx, line.sku, -line.qty, "SALE", order.channel, f"channel:{order.channel}", ref,
                                   ref=order.order_id)
            summary["applied" if result["applied"] else "duplicate"] += 1
        except InsufficientStock as e:
            # Mark the line processed (spec 01): a redelivery or re-poll must not silently consume
            # stock that arrives later. The admin resolves the oversell by hand.
            ctx.store.put_if_absent({"pk": keys.idem(ctx.tenant, ref)[0], "sk": ref,
                                     "result": {"on_hand": e.on_hand, "oversold": True}})
            raise_alert(ctx, "OVERSELL", f"{order.channel} sold {line.qty} of {line.sku} but only {e.on_hand} on hand",
                        {"channel": order.channel, "order_id": order.order_id, "sku": line.sku,
                         "qty": line.qty, "on_hand": e.on_hand}, dedupe_key=f"oversell:{ref}")
            summary["oversold"] += 1
            ctx.queue.send({"type": "push_stock", "tenant": ctx.tenant, "sku": line.sku})
    return summary


def poll_channel(ctx: Ctx, adapter: ChannelAdapter, lookback: timedelta = timedelta(days=1)) -> dict:
    pk, sk = keys.item(ctx.tenant, "CURSOR", adapter.name)
    cursor = ctx.store.get(pk, sk)
    since = cursor["since"] if cursor else (ctx.clock() - lookback).strftime("%Y-%m-%dT%H:%M:%SZ")
    orders, next_cursor = adapter.fetch_orders(since)
    totals = {"orders": len(orders), "applied": 0, "duplicate": 0, "unmapped": 0, "oversold": 0}
    for order in orders:
        for k, v in ingest_order(ctx, order).items():
            totals[k] += v
    ctx.store.put({"pk": pk, "sk": sk, "since": next_cursor, "polled_at": ctx.now_iso()})
    return totals
