# Spec 02 — WhatsApp inventory flow

## Problem
Weavers manage stock from a phone, in their own language, without an app.

## Example
Weaver sends `ADD KS-0114 2` (or `KS-0114 २ जोड़ें`). Reply: "Add 2 to KS-0114 (Kanchi Silk, red)? Reply YES or NO." Weaver sends `हाँ`. Reply: "Done. KS-0114 now has 3."

## Acceptance criteria
1. Security, in this order, before any side effect: secret path token (constant-time compare) → Twilio `X-Twilio-Signature` HMAC-SHA1 validation → MessageSid dedup → sender allowlist (phone must belong to an active supplier).
2. Rejected requests return 403 (token/signature) or a polite "not registered" reply (allowlist) and write nothing to stock.
3. Supported intents: `add`, `sold`, `set` (count correction), `query`, `confirm`, `cancel`, `help`, `new_product` (photo), `unknown`.
4. Parser accepts English keywords, `+N`/`-N` shorthand, Indic-script digits, and keywords in the pilot languages (hi, mr, bn, gu, ta, te, or, as, kn). Free-form text the rules cannot parse goes to the LLM fallback, whose output is schema-validated.
5. No stock write without the weaver's explicit confirmation. Pending confirmations expire after 15 minutes.
6. A weaver can only change stock for SKUs supplied by them.
7. Confirmed changes go through the ledger with idempotency key = pending confirmation id.
8. A photo creates a draft product and an intelligence job; the reply gives the new short code.
9. Eval: intent accuracy ≥ 0.95, SKU accuracy ≥ 0.97, quantity accuracy ≥ 0.97 on `evals/datasets/whatsapp_messages.jsonl`.

## Out of scope
Voice notes (phase 2: transcribe then reuse this parser), buyer-side WhatsApp.

## Failure behavior
Unknown SKU → reply with "not found" and the closest SKUs owned by that weaver. Parser unsure → ask, never guess.
