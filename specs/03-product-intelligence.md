# Spec 03 — Product intelligence

## Problem
Listing a handloom saree needs accurate attributes (fabric, weave, colors, motif, border, zari). Typing them is slow and error-prone.

## Acceptance criteria
1. Input: one or more product photos (S3 keys). Output: `proposed_attributes` with a value and confidence (0–1) per field: fabric, weave, surface (print/dye/paint/embroidery), primary_color, secondary_colors, motif, border, zari.
2. Fabric is normalized to one of the fabric profiles in `vocab.py` or `null`. Synonyms map (e.g. "Kanchipuram silk" → "Kanchi Silk").
3. Model output is parsed and validated; invalid JSON or out-of-vocabulary values become `null` with confidence 0, never an exception that loses the job.
4. Proposals never auto-approve. `attribute_status` is `pending_review` until an admin approves (optionally with edits).
5. Any field with confidence < 0.80 is listed in `needs_review_fields`, and the product sorts first in the review queue.
6. Approved attributes (not proposed ones) are the only attributes shown to buyers and pushed to channels.
7. The model client is an interface; Bedrock is the default implementation.
8. Eval: fabric accuracy ≥ 0.85 and primary color accuracy ≥ 0.80 on the labeled set. The seed set in `evals/datasets/` uses recorded model outputs; replace with 100 real labeled photos before relying on the number.

## Out of scope
Drape rendering, cut layout (fashion engine — phase 2).
