# Simmerca backend

One inventory truth for artisan commerce, sold on many channels and managed by weavers over WhatsApp.
Aalora is the first tenant. Everything server-side runs on AWS; the frontend is Lovable.

```
 Weavers (WhatsApp) ─► Twilio ─► /webhooks/whatsapp ─┐
 Lovable (storefront, admin, wholesale) ─► /public /admin /b2b ─┤   API Gateway (HTTP) + Cognito
 Shopify orders/create ─► /webhooks/shopify ─────────┘                  │
                                                                         ▼
                                                              Lambda: api_handler
                                    ┌──────────────── DynamoDB (single table: products, ledger, ...)
                                    │                 S3 (private images, presigned)
 EventBridge 10 min ─► poller (Etsy orders)           Secrets Manager (all channel credentials)
 SQS sync ─► sync_worker ─► Shopify · Etsy · Meta catalog (absolute qty + price)
 SQS intel ─► intelligence_worker ─► Bedrock vision ─► proposed attributes ─► human review
 CloudWatch alarms ─► SNS ─► email + GitHub issue (agent:triage)
```

## What's in the box
| Area | Where | Spec |
|---|---|---|
| Stock ledger (atomic, idempotent, never negative, made-to-order) | `src/simmerca/ledger.py` | 01 |
| WhatsApp inventory, 10 languages, confirm-before-write | `src/simmerca/whatsapp/` | 02 |
| Product intelligence (photo → attributes, human approval) | `src/simmerca/intelligence/` | 03 |
| Channels: Aalora, Shopify, Etsy, Meta + sync engine | `src/simmerca/channels/` | 04 |
| Pricing: rule, floor, tiers, suggestions, approvals | `src/simmerca/pricing.py` | 05 |
| B2B buyers and quotes | `src/simmerca/b2b.py` | 06 |
| HTTP API + buyer-privacy whitelists | `src/simmerca/api/`, `views.py`, `openapi.yaml` | 07 |
| AWS infrastructure (CDK, staging + prod) | `infra/` | — |
| Loop engineering: agents, hooks, CI gates, agent workflows | `.claude/`, `.github/`, `CLAUDE.md`, `docs/LOOP.md` | — |

## Local
```bash
pip install -r requirements-dev.txt
pytest                      # acceptance + unit + contract (+ DynamoDB via moto)
python evals/run.py         # eval gate
```
No AWS needed for tests: domain code runs against in-memory stores.

## Status (honest)
- Verified here: 80 tests passing (acceptance per spec, unit, API contract, end-to-end journey through the
  router and Lambda handlers), eval gate passing, Twilio signature matches Twilio's documented vector.
  Mutation checks: leaking supplier data, skipping webhook signature checks, allowing negative stock, and
  dropping one language's keywords each turn the build red. An independent review found 12 issues: 11 fixed (with
  regression tests where testable here); 1 open: whether API Gateway accepts the `https://*.lovable.app`
  wildcard CORS origin. The first staging deploy will tell; if not, list exact preview origins in `infra/stages.py`.
- Verified in CI on first push (could not run in the build environment): `ruff`, moto DynamoDB tests, `cdk synth`.
- Not verified against live services: Shopify, Etsy, Meta, Bedrock, Twilio media download. Each adapter
  has a VERIFY note; contract tests against sandboxes are the first loops (`docs/LOOP.md`).
- WhatsApp parser eval is 100% on a 126-message set written by the same author as the parser, so treat it as a
  floor check, not a real accuracy number, until real weaver messages are added.

Next: `SETUP.md`.
