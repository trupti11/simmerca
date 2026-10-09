"""AWS implementations of the storage/queue/blob/model interfaces. boto3 is imported lazily so the
domain code and tests run without it. Covered by tests/test_aws_integration.py (moto) in CI."""

from __future__ import annotations

import json
import logging
import urllib.request
from decimal import Decimal
from typing import Any

from .errors import ConcurrentModification, DuplicateRequest
from .store import StockFloorViolation, stamp_balance

log = logging.getLogger(__name__)


def _to_dynamo(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_dynamo(v) for v in value]
    return value


def _from_dynamo(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _from_dynamo(v) for k, v in value.items()}
    if isinstance(value, (list, set)):
        return [_from_dynamo(v) for v in value]
    return value


class DynamoStore:
    """Single-table DynamoDB store (pk, sk). Same semantics as InMemoryStore."""

    def __init__(self, table_name: str, client=None):
        import boto3
        from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

        self.table = table_name
        self.client = client or boto3.client("dynamodb")
        self._ser = TypeSerializer()
        self._de = TypeDeserializer()

    def _item(self, item: dict) -> dict:
        return {k: self._ser.serialize(_to_dynamo(v)) for k, v in item.items() if v is not None}

    def _plain(self, av: dict) -> dict:
        return {k: _from_dynamo(self._de.deserialize(v)) for k, v in av.items()}

    @staticmethod
    def _key(pk: str, sk: str) -> dict:
        return {"pk": {"S": pk}, "sk": {"S": sk}}

    def get(self, pk: str, sk: str) -> dict | None:
        if not pk or not sk:  # DynamoDB rejects empty key attributes
            return None
        resp = self.client.get_item(TableName=self.table, Key=self._key(pk, sk), ConsistentRead=True)
        return self._plain(resp["Item"]) if "Item" in resp else None

    def put(self, item: dict) -> None:
        self.client.put_item(TableName=self.table, Item=self._item(item))

    def put_if_absent(self, item: dict) -> bool:
        try:
            self.client.put_item(TableName=self.table, Item=self._item(item),
                                 ConditionExpression="attribute_not_exists(pk)")
            return True
        except self.client.exceptions.ConditionalCheckFailedException:
            return False

    def delete(self, pk: str, sk: str) -> None:
        self.client.delete_item(TableName=self.table, Key=self._key(pk, sk))

    def query(self, pk: str, sk_prefix: str = "", reverse: bool = False, limit: int | None = None) -> list[dict]:
        kwargs: dict = {
            "TableName": self.table,
            "KeyConditionExpression": "pk = :pk" + (" AND begins_with(sk, :p)" if sk_prefix else ""),
            "ExpressionAttributeValues": {":pk": {"S": pk}, **({":p": {"S": sk_prefix}} if sk_prefix else {})},
            "ScanIndexForward": not reverse,
            "ConsistentRead": True,
        }
        out: list[dict] = []
        while True:
            if limit:
                kwargs["Limit"] = limit - len(out)
            resp = self.client.query(**kwargs)
            out.extend(self._plain(i) for i in resp.get("Items", []))
            if (limit and len(out) >= limit) or "LastEvaluatedKey" not in resp:
                return out[:limit] if limit else out
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    def increment(self, pk: str, sk: str, by: int = 1) -> int:
        resp = self.client.update_item(
            TableName=self.table, Key=self._key(pk, sk), UpdateExpression="ADD #v :by",
            ExpressionAttributeNames={"#v": "value"}, ExpressionAttributeValues={":by": {"N": str(by)}},
            ReturnValues="UPDATED_NEW",
        )
        return int(resp["Attributes"]["value"]["N"])

    def apply_stock_change(self, balance_key, delta, idem_key, extra_items, min_result=0, expected_on_hand=None,
                           attempts: int = 5) -> int:
        for _ in range(attempts):
            # Duplicate check FIRST, like InMemoryStore: a retried request returns its original result
            # instead of failing a floor/expectation check against today's balance.
            existing = self.get(*idem_key)
            if existing:
                raise DuplicateRequest(existing.get("result", {}))
            bal = self.get(*balance_key)
            old = int(bal["on_hand"]) if bal else 0
            version = int(bal.get("version", 0)) if bal else 0
            if expected_on_hand is not None and old != expected_on_hand:
                raise ConcurrentModification("balance changed since it was read")
            new = old + delta
            if new < min_result:
                raise StockFloorViolation(old)
            bal_put: dict = {
                "TableName": self.table,
                "Item": self._item({"pk": balance_key[0], "sk": balance_key[1], "on_hand": new, "version": version + 1}),
            }
            if bal:
                bal_put.update(ConditionExpression="#v = :v", ExpressionAttributeNames={"#v": "version"},
                               ExpressionAttributeValues={":v": {"N": str(version)}})
            else:
                bal_put["ConditionExpression"] = "attribute_not_exists(pk)"
            items = [
                {"Put": {"TableName": self.table, "ConditionExpression": "attribute_not_exists(pk)",
                         "Item": self._item({"pk": idem_key[0], "sk": idem_key[1], "result": {"on_hand": new}})}},
                {"Put": bal_put},
                *({"Put": {"TableName": self.table, "Item": self._item(x)}}
                  for x in stamp_balance(extra_items, old, new)),
            ]
            try:
                self.client.transact_write_items(TransactItems=items)
                return new
            except self.client.exceptions.TransactionCanceledException as e:
                reasons = e.response.get("CancellationReasons", [])
                codes = [r.get("Code") for r in reasons]
                if codes and codes[0] == "ConditionalCheckFailed":
                    existing = self.get(*idem_key) or {}
                    raise DuplicateRequest(existing.get("result", {})) from None
                if len(codes) > 1 and codes[1] == "ConditionalCheckFailed":
                    continue  # balance changed under us; re-read and retry
                if "TransactionConflict" in codes:
                    continue  # concurrent transaction on the same items; retry
                raise
        raise ConcurrentModification("stock kept changing; retry later")


class SqsQueue:
    def __init__(self, queue_url: str, client=None):
        import boto3

        self.url = queue_url
        self.client = client or boto3.client("sqs")

    def send(self, message: dict) -> None:
        self.client.send_message(QueueUrl=self.url, MessageBody=json.dumps(message))


class S3BlobStore:
    def __init__(self, bucket: str, client=None):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.client = client or boto3.client("s3", config=Config(signature_version="s3v4"))

    def presign_put(self, key: str, content_type: str, expires: int = 900) -> str:
        return self.client.generate_presigned_url(
            "put_object", Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type}, ExpiresIn=expires)

    def presign_get(self, key: str, expires: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires)

    def get_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)


class Secrets:
    """One JSON secret in Secrets Manager holds every channel credential."""

    def __init__(self, secret_id: str, client=None):
        import boto3

        self.secret_id = secret_id
        self.client = client or boto3.client("secretsmanager")
        self._cache: dict | None = None

    def load(self) -> dict:
        if self._cache is None:
            raw = self.client.get_secret_value(SecretId=self.secret_id)["SecretString"]
            self._cache = json.loads(raw or "{}")
        return self._cache

    def reload(self) -> dict:
        self._cache = None
        return self.load()

    def save(self, updates: dict) -> None:
        data = dict(self.load(), **updates)
        self.client.put_secret_value(SecretId=self.secret_id, SecretString=json.dumps(data))
        self._cache = data


class BedrockVision:
    """VisionModel via the Bedrock Converse API."""

    def __init__(self, model_id: str, client=None):
        import boto3

        self.model_id = model_id
        self.client = client or boto3.client("bedrock-runtime")

    def describe(self, images: list[tuple[bytes, str]], prompt: str) -> str:
        content = [{"image": {"format": mt.split("/")[1].replace("jpg", "jpeg"), "source": {"bytes": data}}}
                   for data, mt in images]
        content.append({"text": prompt})
        resp = self.client.converse(modelId=self.model_id, messages=[{"role": "user", "content": content}],
                                    inferenceConfig={"maxTokens": 800, "temperature": 0})
        return resp["output"]["message"]["content"][0]["text"]


class BedrockText:
    """LlmFallback for the WhatsApp parser."""

    def __init__(self, model_id: str, client=None):
        import boto3

        self.model_id = model_id
        self.client = client or boto3.client("bedrock-runtime")

    def complete_json(self, system: str, user: str) -> str:
        resp = self.client.converse(modelId=self.model_id, system=[{"text": system}],
                                    messages=[{"role": "user", "content": [{"text": user}]}],
                                    inferenceConfig={"maxTokens": 200, "temperature": 0})
        return resp["output"]["message"]["content"][0]["text"]


def twilio_media_fetcher(account_sid: str, auth_token: str):
    """Download Twilio media with basic auth. The Authorization header is NOT forwarded on the
    redirect to the signed storage URL (add_unredirected_header)."""
    import base64

    token = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()

    def fetch(url: str) -> tuple[bytes, str]:
        if not url.startswith("https://api.twilio.com/"):
            raise ValueError("refusing to fetch media from a non-Twilio URL")
        req = urllib.request.Request(url)
        req.add_unredirected_header("Authorization", f"Basic {token}")
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
            data = resp.read(15 * 1024 * 1024 + 1)
            if len(data) > 15 * 1024 * 1024:
                raise ValueError("media too large")
            return data, resp.headers.get("Content-Type", "image/jpeg").split(";")[0]

    return fetch
