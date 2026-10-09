# Decisions log

One line per decision: date — decision — why. Agents read this; do not relitigate settled lines.

- 2026-10-07 — Simmerca owns the backend, data and supplier network; Aalora is the first tenant (brand + Lovable storefront). — Avoid building commerce twice; Simmerca serves other brands later.
- 2026-10-07 — Everything server-side on AWS; Lovable is frontend only. — Owner decision.
- 2026-10-07 — DynamoDB single table instead of Aurora Postgres. — Serverless, no VPC, cheap at MVP scale; TransactWriteItems gives the atomic ledger + balance + idempotency write we need.
- 2026-10-07 — Lambdas use stdlib + boto3 only. — No bundling step, agents can't break builds with native deps.
- 2026-10-07 — Channels: Aalora, Shopify, Etsy, Meta. Amazon/eBay are phase 2. — Amazon SP-API approval takes weeks; start the application now.
- 2026-10-07 — Aalora checkout stays on Shopify headless; Aalora orders arrive through the Shopify webhook. Aalora reads catalog + availability from the Simmerca public API. — Reuses working checkout and payments.
- 2026-10-07 — Meta is catalog sync only (inventory + price). Orders come through Aalora/Shopify checkout. — Meta shop checkout varies by region; website checkout is the reliable path.
- 2026-10-07 — Etsy and Meta inbound: scheduled polling every 10 minutes; Shopify inbound: webhook. — Webhook support differs by channel.
- 2026-10-07 — Channel pushes are absolute quantities via SQS, retried with DLQ. — Replay-safe.
- 2026-10-07 — Product intelligence proposes attributes; a human approves before they are published. Confidence < 0.80 on any field is flagged for priority review. — "AI proposes, humans verify."
- 2026-10-07 — Pricing: rule price (cost + margin, tiers) plus a comparables-based suggestion; nothing goes live without owner approval. No competitor scraping. — Scraping is brittle and can breach site terms.
- 2026-10-07 — Buyer-facing projections are whitelists (`views.py`). — A blacklist leaks the next field someone adds.
- 2026-10-07 — Eval gate includes a per-language floor (0.85) besides overall accuracy. — An average hid a whole language regressing in a mutation test.
- 2026-10-07 — Quote approvals use per-attempt idempotency keys and roll back on any failure. — A rolled-back attempt must never block or corrupt a later approval.
- 2026-10-07 — Oversold channel lines are marked processed and alerted, not retried. — Otherwise later restocks are silently consumed by old orders.
