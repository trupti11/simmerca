"""Spec 07 acceptance tests. OWNER-CONTROLLED: agents may not edit this file."""

import json
import unittest

from helpers import SECRETS, FakeAdapter, api_event, make_ctx, seed

from simmerca import b2b
from simmerca.api.router import App, handle
from simmerca.channels.sync import link_listing

FORBIDDEN_STRINGS = ("Lakshmi Weaver", "+919845012345", "Arni village", "20000", "cost_cents", "supplier",
                     "weaver asked for advance", "internal_notes", "proposed_attributes")


class ApiPrivacyAcceptance(unittest.TestCase):
    def setUp(self):
        self.ctx = make_ctx()
        _, p = seed(self.ctx, stock=3)
        self.sku = p["sku"]
        link_listing(self.ctx, self.sku, "aalora", {"aalora": FakeAdapter("aalora")})
        self.app = App(ctx=self.ctx, secrets=SECRETS)
        b2b.upsert_buyer_profile(self.ctx, "buyer-1", {"company": "Boutique"})
        b2b.set_buyer_status(self.ctx, "buyer-1", "approved", "t")

    def call(self, *args, **kw):
        resp = handle(self.app, api_event(*args, **kw))
        return resp["statusCode"], resp["body"]

    def assert_private(self, body):
        for s in FORBIDDEN_STRINGS:
            self.assertNotIn(s, body, f"buyer-facing response leaked {s!r}")

    def test_04_public_and_b2b_responses_never_leak_supplier_or_cost(self):
        for path in ("/public/products", f"/public/products/{self.sku}"):
            status, body = self.call("GET", path)
            self.assertEqual(status, 200)
            self.assert_private(body)
        status, body = self.call("GET", "/b2b/catalog", groups=["buyer"], sub="buyer-1")
        self.assertEqual(status, 200)
        self.assert_private(body)
        status, body = self.call("POST", "/b2b/quotes", {"lines": [{"sku": self.sku, "qty": 2}]}, groups=["buyer"],
                                 sub="buyer-1")
        self.assertEqual(status, 201)
        self.assert_private(body)

    def test_03_auth_groups_enforced(self):
        self.assertEqual(self.call("GET", "/admin/products")[0], 403)
        self.assertEqual(self.call("GET", "/admin/products", groups=["buyer"])[0], 403)
        self.assertEqual(self.call("GET", "/admin/products", groups=["admin"])[0], 200)
        self.assertEqual(self.call("GET", "/b2b/catalog")[0], 403)

    def test_03_errors_are_clean(self):
        self.assertEqual(self.call("GET", "/nope")[0], 404)
        status, body = self.call("POST", "/admin/products/XX-0001/stock", {"delta": 1}, groups=["admin"])
        self.assertEqual(status, 400)  # missing idempotency key
        self.assertNotIn("Traceback", body)
        status, _ = self.call("GET", "/public/products/KS-9999")
        self.assertEqual(status, 404)

    def test_draft_products_not_public(self):
        _, draft = seed(self.ctx, stock=1, status="draft")
        status, _ = self.call("GET", f"/public/products/{draft['sku']}")
        self.assertEqual(status, 404)
        items = json.loads(self.call("GET", "/public/products")[1])["items"]
        self.assertEqual([i["sku"] for i in items], [self.sku])

    def test_06_image_upload_presigned(self):
        status, body = self.call("POST", f"/admin/products/{self.sku}/images", {"content_type": "image/png"},
                                 groups=["admin"])
        self.assertEqual(status, 201)
        data = json.loads(body)
        self.assertTrue(data["upload_url"].startswith("https://"))
        self.assertTrue(data["key"].endswith(".png"))


if __name__ == "__main__":
    unittest.main()
