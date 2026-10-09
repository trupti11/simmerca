"""DynamoStore against moto (runs in CI where boto3 + moto are installed; skipped otherwise)."""

import os
import unittest

import helpers  # noqa: F401

from simmerca.errors import DuplicateRequest
from simmerca.store import StockFloorViolation

try:
    import boto3
    from moto import mock_aws
except ImportError:  # pragma: no cover
    boto3 = None


@unittest.skipIf(boto3 is None, "boto3/moto not installed")
class DynamoStoreIntegration(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
        os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
        os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
        self.mock = mock_aws()
        self.mock.start()
        ddb = boto3.client("dynamodb")
        ddb.create_table(TableName="t", BillingMode="PAY_PER_REQUEST",
                         KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"},
                                    {"AttributeName": "sk", "KeyType": "RANGE"}],
                         AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                               {"AttributeName": "sk", "AttributeType": "S"}])
        from simmerca.aws import DynamoStore

        self.store = DynamoStore("t", ddb)

    def tearDown(self):
        self.mock.stop()

    def test_crud_query_increment_and_floats(self):
        self.store.put({"pk": "P", "sk": "a", "n": 1, "conf": 0.92, "nested": {"x": [1, 2.5]}})
        self.store.put({"pk": "P", "sk": "b"})
        self.assertFalse(self.store.put_if_absent({"pk": "P", "sk": "a"}))
        self.assertEqual(self.store.get("P", "a")["nested"], {"x": [1, 2.5]})
        self.assertEqual(self.store.get("P", "a")["conf"], 0.92)
        self.assertEqual([r["sk"] for r in self.store.query("P", reverse=True)], ["b", "a"])
        self.assertEqual(self.store.increment("C", "x"), 1)
        self.assertEqual(self.store.increment("C", "x"), 2)

    def test_stock_change_atomic_idempotent_and_floor(self):
        bal = ("B", "KJ-0001")
        ev = {"pk": "L", "sk": "1"}
        self.assertEqual(self.store.apply_stock_change(bal, 3, ("I", "k1"), [ev]), 3)
        with self.assertRaises(DuplicateRequest):
            self.store.apply_stock_change(bal, 3, ("I", "k1"), [])
        with self.assertRaises(StockFloorViolation):
            self.store.apply_stock_change(bal, -5, ("I", "k2"), [])
        self.assertEqual(self.store.apply_stock_change(bal, -1, ("I", "k3"), []), 2)
        self.assertEqual(self.store.get(*bal)["on_hand"], 2)
        self.assertIsNotNone(self.store.get("L", "1"))


if __name__ == "__main__":
    unittest.main()
