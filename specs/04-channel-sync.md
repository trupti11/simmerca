# Spec 04 — Channel sync: Aalora, Shopify, Etsy, Meta

## Problem
Selling on four channels from one inventory without overselling.

## Acceptance criteria
1. A listing links a sku to a channel with that channel's external ids. Without a listing, a channel is not touched.
2. Outbound: `push_stock` and `push_price` jobs send ABSOLUTE values to every linked channel. Replaying a job is harmless.
3. Inbound orders:
   - Shopify (also carries Aalora storefront checkout): `orders/create` webhook, verified with `X-Shopify-Hmac-Sha256` before parsing.
   - Etsy: poll receipts every 10 minutes from a stored cursor.
   - Meta: catalog only (inventory + price + availability); no order ingest.
   - Aalora: reads catalog and availability live from the public API; no push needed.
4. Each order line becomes a ledger `SALE` with idempotency key `<channel>:<order_id>:<line_id>`. A webhook delivered twice decrements once.
5. Lines whose sku is unknown create an `UNMAPPED_SKU` alert; they do not fail the whole order.
6. Channel API errors raise; the SQS job is retried and lands in the DLQ after 5 attempts. The DLQ has an alarm.
7. All channel HTTP goes through one client with timeouts and retries on 429/5xx with backoff.

## Out of scope
Amazon, eBay (phase 2 — start Amazon SP-API registration now), creating listings on channels automatically (owner links existing listings; `link` helpers discover ids by sku).
