# Spec 05 — Pricing

## Acceptance criteria
1. Rule price = cost × (1 + margin) ÷ (1 − channel fee), rounded UP to a whole currency unit. Integer cents throughout.
2. Floor price = cost × (1 + minimum margin). No computed or suggested price is ever below the floor.
3. Quantity tiers apply a discount to the retail price for B2B buyers (default: 1–4 → 0%, 5–9 → 10%, 10+ → 18%), clamped at the floor.
4. Suggestion: median price of comparables (own catalog items with the same fabric, plus owner-entered comparables), clamped to [floor, 2 × rule price]. The response explains the inputs used.
5. Price changes are proposals. A proposal goes live only when an admin approves it; approval updates the product and enqueues `push_price`.
6. No competitor scraping.
