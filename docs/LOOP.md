# Running the loop

You own specs, acceptance tests, eval thresholds, merges and prod. Agents do the rest.

```
 you: spec + acceptance tests ──► planner ──► builder ──► tester ──► CI gate (lint, tests, evals, synth)
        ▲                                                                   │ red: back to builder
        │                                                                   ▼ green
  ops agent triages ◄── alarms (DLQ, errors, 5xx) ◄── staging ◄── you merge ◄── reviewer agent
  into new issues                                        │
                                                         └──► you approve ──► prod
```

## Daily routine (1–2 hours)
**Morning — inputs (30–45 min)**
1. Read new `agent:triage` issues and the reviewer's comments.
2. For the next feature: write or update `specs/NN-*.md` and `tests/acceptance_NN_*_test.py`. Merge them yourself.
3. Open an issue from the "Feature loop" template and add the label `agent:build`.

**Evening — gates (30–45 min)**
1. Open PRs from the agent: CI green? Reviewer APPROVE? Read the diff where money, stock, auth or privacy is touched.
2. Merge → staging deploys and smoke-tests itself.
3. Try it in staging (WhatsApp from your phone, Lovable admin). Approve `production` in GitHub Actions when happy.

## Locally with Claude Code
```bash
cd simmerca-backend && claude
> Use the planner subagent on specs/04-channel-sync.md and show me the plan.
> Use the builder subagent for task 1 of the plan.
> Use the tester subagent on the current diff.
> Use the reviewer subagent on the current branch.
```
Hooks already block edits to owner files and run the tests after every Python edit.

## Rules that keep it safe
- If an agent repeats a mistake, fix `CLAUDE.md` (or the spec), not the code.
- Agents never edit acceptance tests, eval thresholds/datasets, workflows, hooks or `infra/stages.py` (hook-enforced).
- Prod deploys need your approval in the `production` environment. Agent credentials cannot reach prod.
- A failed eval is a red build. Lower a threshold only by deliberate decision, logged in `decisions.md`.

## Weekly review (Friday, 20 min)
| Metric | Where | Healthy |
|---|---|---|
| PRs merged within 2 loops | GitHub PRs | most |
| Your review time per day | your calendar | ≤ 2 h |
| Escaped bugs to staging | `agent:triage` issues | falling |
| Eval scores | CI artifact `eval-results` | stable or rising |
| AWS + model cost | AWS Budgets email, Anthropic console | under cap |

## Starter backlog (first loops)
1. Shopify contract test against a dev store; fix adapter if the API shape changed.
2. Etsy contract test on one listing.
3. Meta contract test on a test catalog.
4. Native-speaker keyword review; add reply templates for mr, bn, gu, ta, te, or, as, kn.
5. Real attribute eval set (100 labeled photos) + `--live` eval in a weekly scheduled workflow.
6. Voice notes: transcribe, then reuse the WhatsApp parser.
7. Phase 2 channels: Amazon SP-API, eBay. Shipping: DTDC (India), FedEx (US).
8. Fashion customization engine spike (fabric → cut layout → render), as its own spec.
