# Simmerca backend — agent context

Read this file fully before every task. It is the contract between the owner (Trupti) and every agent.

## What this is
Simmerca is the commerce + object-intelligence backend. Aalora is the first tenant (brand + storefront).
One inventory truth, many channels: Aalora storefront, Shopify, Etsy, Meta catalog.
Suppliers (weavers) manage stock over WhatsApp. AI proposes; humans verify.

## Stack (do not change without a decision in decisions.md)
- Python 3.12, standard library + boto3 only inside Lambdas (no compiled deps, no bundling)
- AWS: API Gateway HTTP API, Lambda, DynamoDB (single table), S3, SQS (+DLQ), EventBridge schedules,
  Cognito (admin + buyer groups), Secrets Manager, Bedrock (vision + text), CloudWatch alarms, Budgets
- Infra as code: AWS CDK v2 (Python) in `infra/`
- Frontend: Lovable (React). It talks ONLY to the HTTP API described in `openapi.yaml`.

## Layout
- `src/simmerca/` domain code. Domain modules never import boto3 directly; they take a `Store`/`Queue`.
  - `store.py` storage interface + InMemoryStore (tests) + DynamoStore (prod)
  - `ledger.py` stock ledger (every change is an event; balances are derived and reconciled)
  - `products.py`, `pricing.py`, `b2b.py`, `views.py` (buyer-safe projections)
  - `whatsapp/` Twilio security, multilingual parser, confirm-before-write flow
  - `intelligence/` photo → attributes with confidence; never auto-approves
  - `channels/` one adapter per marketplace + `sync.py` (outbound pushes, inbound orders)
  - `api/` HTTP router for API Gateway; `lambdas.py` Lambda entrypoints
- `tests/` acceptance tests (`acceptance_*_test.py`, owner-written) + unit tests (`test_*.py`) + contract test vs `openapi.yaml`
- `evals/` AI eval harness, datasets and thresholds
- `specs/` one spec per feature loop. Specs are the source of truth.

## Commands
- Tests: `pytest` (or `PYTHONPATH=src:tests python -m unittest discover -s tests -p "*test*.py"`)
- Evals: `PYTHONPATH=src python evals/run.py`
- Lint: `ruff check src tests evals infra`
- Infra: `cd infra && cdk synth -c stage=staging`

## Conventions
- Money is integer minor units (`price_cents`) + `currency`. Never floats for money.
- Quantities are ints. Stock never goes negative (enforced in the store transaction).
- Every write that can be retried takes an idempotency key.
- Channel pushes send ABSOLUTE quantities, never deltas (safe to replay).
- Buyer-facing output goes through `views.py` whitelists. Never return a raw product item to a buyer.
- Small functions, type hints, docstrings on public functions. No print(); use `logging`.

## Never do (stop and ask the owner instead)
- Edit anything in `tests/acceptance_*`, `evals/thresholds.json` or `evals/datasets/`
- Touch prod: deploy, run prod migrations, read prod secrets
- Add a dependency, AWS service or paid API not listed above
- Change live prices or message suppliers/buyers outside tested flows
- Expose supplier identity (name, phone, location, cost) in any buyer-facing response
- Reinterpret a spec. If the spec looks wrong, write the question in the PR and stop.

## Definition of done for a task
1. Acceptance tests for the spec pass, plus all existing tests
2. Evals at or above thresholds (if the task touches AI)
3. `ruff` clean
4. PR description: what changed, which spec criteria it satisfies, anything left open
