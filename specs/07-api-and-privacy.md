# Spec 07 — HTTP API and buyer privacy

## Acceptance criteria
1. One HTTP API (API Gateway) described by `openapi.yaml`. The Lovable frontend uses only this contract.
2. Route groups: `/public/*` (no auth), `/b2b/*` (Cognito group `buyer`), `/admin/*` (group `admin`), `/webhooks/*` (verified by signature).
3. Wrong or missing group → 403. Unknown route → 404. Validation error → 400 with a message. Errors never include stack traces.
4. Buyer-facing responses are built from whitelists in `views.py`. Fields never present: supplier_id, supplier name, supplier phone, supplier location, cost_cents, proposed (unapproved) attributes, internal notes.
5. The public story text is owner-written (`story_public`); the system never inserts supplier fields into it.
6. Image uploads use presigned S3 PUT URLs; images are served through presigned GET URLs (private bucket).
