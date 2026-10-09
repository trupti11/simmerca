#!/usr/bin/env python3
"""cdk synth -c stage=staging | cdk deploy -c stage=prod | cdk deploy -c stage=staging -c oidc=1"""

import aws_cdk as cdk
from stack import GithubOidcStack, SimmercaStack
from stages import STAGES

app = cdk.App()
stage = app.node.try_get_context("stage") or "staging"
if stage not in STAGES:
    raise SystemExit(f"unknown stage {stage}; use one of {list(STAGES)}")
cfg = STAGES[stage]
env = cdk.Environment(account=cfg["account"], region=cfg["region"])

if app.node.try_get_context("oidc"):
    GithubOidcStack(app, f"SimmercaGithubOidc-{stage}", repo=cfg["github_repo"], stage=stage, env=env)
else:
    SimmercaStack(app, f"Simmerca-{stage}", stage=stage, cfg=cfg, env=env)
    cdk.Tags.of(app).add("project", "simmerca")
    cdk.Tags.of(app).add("stage", stage)

app.synth()
