"""Lambda entrypoints and the composition root. All configuration comes from environment variables
set by the CDK stack; all credentials come from one Secrets Manager secret."""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from functools import lru_cache

from .api.router import App, handle
from .channels.aalora import AaloraChannel
from .channels.etsy import EtsyChannel, EtsyTokens
from .channels.http import HttpClient
from .channels.meta import MetaCatalogChannel
from .channels.shopify import DEFAULT_API_VERSION as DEFAULT_SHOPIFY_VERSION
from .channels.shopify import ShopifyChannel
from .channels.sync import poll_channel, process_job
from .context import Ctx
from .intelligence.extractor import ingest_media, propose_attributes

logging.getLogger().setLevel(os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _aws():
    from . import aws

    secrets = aws.Secrets(os.environ["SECRET_ARN"])
    ctx = Ctx(
        store=aws.DynamoStore(os.environ["TABLE_NAME"]),
        queue=aws.SqsQueue(os.environ["SYNC_QUEUE_URL"]),
        blobs=aws.S3BlobStore(os.environ["BUCKET_NAME"]),
        tenant=os.environ.get("TENANT", "aalora"),
        intelligence_queue=aws.SqsQueue(os.environ["INTEL_QUEUE_URL"]) if os.environ.get("INTEL_QUEUE_URL") else None,
        settings=json.loads(os.environ.get("PRICING_SETTINGS", "{}")),
    )
    return aws, secrets, ctx


def build_adapters(s: dict, http: HttpClient | None = None, save_secret=None, reload_secret=None) -> dict:
    """Only channels whose credentials exist in the secret are enabled."""
    http = http or HttpClient()
    adapters: dict = {"aalora": AaloraChannel()}
    if s.get("shopify_shop_domain") and s.get("shopify_access_token"):
        adapters["shopify"] = ShopifyChannel(s["shopify_shop_domain"], s["shopify_access_token"],
                                             s.get("shopify_location_id", ""), http,
                                             s.get("shopify_api_version") or DEFAULT_SHOPIFY_VERSION)
    if s.get("etsy_keystring") and s.get("etsy_refresh_token") and s.get("etsy_shop_id"):
        tokens = EtsyTokens(s["etsy_keystring"], s["etsy_refresh_token"], http, save=save_secret,
                            reload=(lambda: reload_secret().get("etsy_refresh_token")) if reload_secret else None)
        adapters["etsy"] = EtsyChannel(s["etsy_keystring"], s["etsy_shop_id"], tokens, http,
                                       s.get("etsy_shared_secret"))
    if s.get("meta_catalog_id") and s.get("meta_access_token"):
        adapters["meta"] = MetaCatalogChannel(s["meta_catalog_id"], s["meta_access_token"], http,
                                              s.get("meta_graph_version") or "v23.0")
    return adapters


@lru_cache(maxsize=1)
def _app() -> App:
    aws, secrets, ctx = _aws()
    s = secrets.load()
    text_model = os.environ.get("BEDROCK_TEXT_MODEL_ID")
    return App(ctx=ctx, adapters=build_adapters(s, save_secret=secrets.save, reload_secret=secrets.reload), secrets=s,
               llm=aws.BedrockText(text_model) if text_model else None,
               public_base_url=os.environ.get("PUBLIC_BASE_URL") or None)


# ------------------------------------------------------------------ handlers


def api_handler(event, context):
    return handle(_app(), event)


def sync_worker(event, context):
    """SQS batch; failed messages are retried individually (ReportBatchItemFailures)."""
    app = _app()
    failures = []
    for record in event.get("Records", []):
        try:
            process_job(app.ctx, json.loads(record["body"]), app.adapters)
        except Exception:  # noqa: BLE001
            log.exception("sync job failed: %s", record.get("messageId"))
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}


def intelligence_worker(event, context):
    aws, secrets, ctx = _aws()
    s = secrets.load()
    model_id = os.environ.get("BEDROCK_VISION_MODEL_ID")
    model = aws.BedrockVision(model_id) if model_id else None
    failures = []
    for record in event.get("Records", []):
        try:
            job = json.loads(record["body"])
            if job["type"] == "propose":
                if model is None:
                    raise RuntimeError("BEDROCK_VISION_MODEL_ID is not configured")
                propose_attributes(ctx, job["sku"], model)
            elif job["type"] == "ingest_media":
                fetch = aws.twilio_media_fetcher(s.get("twilio_account_sid", ""), s.get("twilio_auth_token", ""))
                ingest_media(ctx, job["sku"], job.get("media_urls", []), fetch, model)
            else:
                log.warning("unknown intelligence job %s", job.get("type"))
        except Exception:  # noqa: BLE001
            log.exception("intelligence job failed: %s", record.get("messageId"))
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}


def poller(event, context):
    """EventBridge schedule: pull orders from channels without webhooks (Etsy)."""
    app = _app()
    results = {}
    for name in ("etsy",):
        if name in app.adapters:
            results[name] = poll_channel(app.ctx, app.adapters[name])
    log.info("poll results %s", results)
    return results


def cognito_post_confirmation(event, context):
    """Every self-registered user becomes a (pending) buyer. Admins are added to `admin` by hand."""
    import boto3

    boto3.client("cognito-idp").admin_add_user_to_group(
        UserPoolId=event["userPoolId"], Username=event["userName"], GroupName="buyer")
    return event


def alarm_to_issue(event, context):
    """SNS (CloudWatch alarm) -> GitHub issue labelled agent:triage, so the ops agent picks it up."""
    _, secrets, _ = _aws()
    token = secrets.load().get("github_token")
    repo = os.environ.get("GITHUB_REPO")
    if not token or not repo:
        log.warning("github_token or GITHUB_REPO missing; alarm not forwarded")
        return {"forwarded": 0}
    count = 0
    for record in event.get("Records", []):
        msg = json.loads(record["Sns"]["Message"])
        if msg.get("NewStateValue") != "ALARM":
            continue
        body = json.dumps({
            "title": f"[{os.environ.get('STAGE', '?')}] Alarm: {msg.get('AlarmName')}",
            "body": f"**Reason:** {msg.get('NewStateReason')}\n\n**Time:** {msg.get('StateChangeTime')}\n\n"
                    f"```json\n{json.dumps(msg.get('Trigger', {}), indent=2)[:3000]}\n```\n\n"
                    "Ops agent: triage per `.claude/agents/ops.md`.",
            "labels": ["agent:triage"],
        }).encode()
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/issues", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {token}",
                                              "Accept": "application/vnd.github+json",
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10):  # noqa: S310
            count += 1
    return {"forwarded": count}
