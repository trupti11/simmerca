"""End-to-end journey through the HTTP router and Lambda handlers (no AWS): admin sets up a product,
AI proposes attributes, a weaver adds stock on WhatsApp, Shopify sells, channels sync, a buyer gets a quote."""

import json
import unittest
from unittest import mock

from helpers import BASE_URL, SECRETS, SHOPIFY_SECRET, FakeAdapter, api_event, form_body, make_ctx, sign, twilio_params

from simmerca import lambdas
from simmerca.api.router import App, handle


class FakeVision:
    def describe(self, images, prompt):
        return json.dumps({"fabric": {"value": "Kanjivaram", "confidence": 0.9},
                           "primary_color": {"value": "red", "confidence": 0.9}})


class Journey(unittest.TestCase):
    def setUp(self):
        self.ctx = make_ctx()
        self.shopify = FakeAdapter("shopify")
        self.app = App(ctx=self.ctx, adapters={"shopify": self.shopify, "aalora": FakeAdapter("aalora")},
                       secrets=SECRETS, public_base_url=BASE_URL)

    def call(self, method, path, body=None, groups=("admin",), sub="owner", **kw):
        resp = handle(self.app, api_event(method, path, body, groups=list(groups) if groups else None, sub=sub, **kw))
        data = json.loads(resp["body"]) if resp["headers"].get("Content-Type") == "application/json" else resp["body"]
        return resp["statusCode"], data

    def whatsapp(self, text, n):
        params = twilio_params(text, f"SM{n:032d}")
        path = f"/webhooks/whatsapp/{SECRETS['whatsapp_path_token']}"
        return self.call("POST", path, groups=None, raw=form_body(params),
                         headers={"x-twilio-signature": sign(params, BASE_URL + path),
                                  "content-type": "application/x-www-form-urlencoded"})

    def sync(self):
        """Run queued jobs through the real Lambda handler, like SQS would."""
        records = [{"messageId": str(i), "body": json.dumps(j)} for i, j in enumerate(self.ctx.queue.drain())]
        with mock.patch.object(lambdas, "_app", return_value=self.app):
            return lambdas.sync_worker({"Records": records}, None)

    def test_full_journey(self):
        # admin: supplier + product
        st, sup = self.call("POST", "/admin/suppliers", {"name": "Lakshmi", "phone": "+919845012345", "language": "hi"})
        self.assertEqual(st, 201)
        st, p = self.call("POST", "/admin/products", {"fabric": "Kanjivaram", "supplier_id": sup["id"],
                                                      "cost_cents": 20000, "price_cents": 45000,
                                                      "story_public": "Woven over three weeks."})
        self.assertEqual((st, p["sku"]), (201, "KJ-0001"))
        sku = p["sku"]

        # image + AI proposal + human approval
        st, up = self.call("POST", f"/admin/products/{sku}/images", {"content_type": "image/jpeg"})
        self.ctx.blobs.put_bytes(up["key"], b"\xff\xd8", "image/jpeg")
        st, _ = self.call("POST", f"/admin/products/{sku}/intelligence")
        self.assertEqual(st, 202)
        job = self.ctx.intelligence_queue.drain()[0]
        with mock.patch.object(lambdas, "_aws", return_value=(mock.Mock(BedrockVision=lambda m: FakeVision()),
                                                              mock.Mock(load=lambda: {}), self.ctx)), \
                mock.patch.dict("os.environ", {"BEDROCK_VISION_MODEL_ID": "test-model"}):
            out = lambdas.intelligence_worker({"Records": [{"messageId": "1", "body": json.dumps(job)}]}, None)
        self.assertEqual(out["batchItemFailures"], [])
        st, queue = self.call("GET", "/admin/review-queue")
        self.assertEqual([q["sku"] for q in queue["items"]], [sku])
        st, _ = self.call("POST", f"/admin/products/{sku}/attributes/approve", {"edits": {"motif": "temple"}})
        self.assertEqual(st, 200)

        # activate + publish to storefront and Shopify
        st, _ = self.call("PATCH", f"/admin/products/{sku}", {"status": "active"})
        self.assertEqual(st, 200)
        for ch in ("aalora", "shopify"):
            st, _ = self.call("POST", f"/admin/products/{sku}/listings", {"channel": ch})
            self.assertEqual(st, 201)
        self.sync()

        # weaver adds stock on WhatsApp (Hindi confirmation)
        st, body = self.whatsapp(f"{sku} २ जोड़ें", 1)
        self.assertEqual(st, 200)
        self.assertIn("हाँ", body)
        st, body = self.whatsapp("हाँ", 2)
        self.assertIn("2", body)
        self.assertEqual(self.sync()["batchItemFailures"], [])
        self.assertEqual(self.shopify.calls[-1][:3], ("inventory", sku, 2))

        # storefront shows it
        st, cat = self.call("GET", "/public/products", groups=None)
        self.assertEqual(cat["items"][0]["availability"], {"status": "in_stock", "quantity": 2, "lead_time_days": 0})
        self.assertEqual(cat["items"][0]["attributes"]["motif"], "temple")

        # Shopify sells one (webhook delivered twice)
        import base64
        import hashlib
        import hmac

        raw = json.dumps({"id": 9001, "line_items": [{"id": 1, "sku": sku, "quantity": 1}]}).encode()
        sig = base64.b64encode(hmac.new(SHOPIFY_SECRET.encode(), raw, hashlib.sha256).digest()).decode()
        for _ in range(2):
            st, _ = self.call("POST", "/webhooks/shopify", groups=None, raw=raw,
                              headers={"x-shopify-hmac-sha256": sig, "x-shopify-topic": "orders/create"})
            self.assertEqual(st, 200)
        self.sync()
        self.assertEqual(self.shopify.calls[-1][:3], ("inventory", sku, 1))

        # wholesale buyer
        st, _ = self.call("PUT", "/b2b/me", {"company": "Boutique NYC"}, groups=("buyer",), sub="b1")
        st, _ = self.call("GET", "/b2b/catalog", groups=("buyer",), sub="b1")
        self.assertEqual(st, 403)  # not approved yet
        self.call("POST", "/admin/buyers/b1/status", {"status": "approved"})
        st, q = self.call("POST", "/b2b/quotes", {"lines": [{"sku": sku, "qty": 1}]}, groups=("buyer",), sub="b1")
        self.assertEqual(st, 201)
        st, _ = self.call("POST", f"/admin/quotes/{q['id']}/approve", {"message": "Ships Monday"})
        self.assertEqual(st, 200)
        st, mine = self.call("GET", f"/b2b/quotes/{q['id']}", groups=("buyer",), sub="b1")
        self.assertEqual((mine["status"], mine["message"]), ("approved", "Ships Monday"))

        # ledger reconciles and dashboard is consistent
        st, rec = self.call("GET", f"/admin/products/{sku}/reconcile")
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["balance"], 0)
        st, dash = self.call("GET", "/admin/dashboard")
        self.assertEqual(dash["products"]["active"], 1)
        self.assertIn(sku, dash["low_stock_skus"])

    def test_sync_worker_reports_only_failed_messages(self):
        self.app.adapters["meta"] = FakeAdapter("meta", fail=True)
        st, p = self.call("POST", "/admin/products", {"fabric": "Tussar", "price_cents": 100, "status": "active"})
        self.call("PATCH", f"/admin/products/{p['sku']}", {"status": "active"})
        self.call("POST", f"/admin/products/{p['sku']}/listings", {"channel": "meta"})
        out = self.sync()
        self.assertTrue(out["batchItemFailures"])  # meta failed -> SQS will retry those messages


if __name__ == "__main__":
    unittest.main()
