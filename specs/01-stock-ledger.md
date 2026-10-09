# Spec 01 — Stock ledger and product model

## Problem
Stock lives in many places (weaver's memory, Shopify, Etsy, Meta). We need one truth that every channel reconciles against.

## Example
Weaver adds 2 of KS-114. Ledger records `RECEIVE +2`. Balance becomes 2. Shopify sells 1 → `SALE -1`, balance 1, all channels receive "1".

## Acceptance criteria
1. Every stock change is an immutable ledger event: sku, delta, reason, source, actor, ref, idempotency key, timestamp.
2. Balance changes and the event write happen atomically; stock can never go negative (the write is rejected with `InsufficientStock`).
3. The same idempotency key applied twice changes stock once and returns the same resulting balance.
4. `reconcile(sku)` sums events and reports any mismatch with the stored balance.
5. Made-to-order products never show as "in stock": availability is `made_to_order` with `lead_time_days`; channels receive `mto_capacity` as the sellable quantity, and an MTO sale creates a production order instead of decrementing stock.
6. Every applied change enqueues one `push_stock` job for that sku.
7. Products have a short code SKU (fabric prefix + number, e.g. `KS-0001`) that a weaver can read off a hang tag.
8. Internal fields (supplier_id, cost_cents) live on the product; they are never exposed by buyer views (see spec 07).

## Out of scope
Multi-location stock, reservations with expiry, returns workflow UI.

## Data touched
Product, Balance, LedgerEvent, Idempotency marker, ProductionOrder, Counter.

## Failure behavior
Insufficient stock on a channel sale → write an `OVERSELL` alert, do not go negative, still mark the order line as processed so retries don't loop.
