"""Spec 04 acceptance tests. OWNER-CONTROLLED: agents may not edit this file."""

import base64
import hashlib
import hmac
import json
import unittest

from helpers import SECRETS, SHOPIFY_SECRET, FakeAdapter, api_event, make_ctx, seed

from simmerca import alerts, ledger
from simmerca.api.router import App, handle
from simmerca.channels.base import ChannelOrder, OrderLine
from simmerca.channels.sync import ingest_order, link_listing, poll_channel, process_job
from simmerca.errors import ChannelError


class ChannelAcceptance(unittest.TestCase):
    def setUp(self):
        self.ctx = make_ctx()
        _, p = seed(self.ctx, stock=3)
        self.sku = p["sku"]
        self.shopify, self.etsy, self.meta = FakeAdapter("shopify"), FakeAdapter("etsy"), FakeAdapter("meta")
        self.adapters = {"shopify": self.shopify, "etsy": self.etsy, "meta": self.meta}

    def run_jobs(self):
        for job in self.ctx.queue.drain():
            process_job(self.ctx, job, self.adapters)

    def test_01_unlinked_channels_untouched(self):
        link_listing(self.ctx, self.sku, "shopify", self.adapters)
        self.run_jobs()
        self.assertTrue(self.shopify.calls)
        self.assertFalse(self.etsy.calls)

    def test_02_pushes_absolute_quantities_and_replay_is_harmless(self):
        for ch in ("shopify", "etsy", "meta"):
            link_listing(self.ctx, self.sku, ch, self.adapters)
        self.run_jobs()
        ledger.record_change(self.ctx, self.sku, -1, "SALE", "etsy", "t", "etsy:1:1")
        jobs = self.ctx.queue.drain()
        for job in jobs + jobs:  # replay
            process_job(self.ctx, job, self.adapters)
        inv = [c for c in self.shopify.calls if c[0] == "inventory"]
        self.assertEqual(inv[-1], ("inventory", self.sku, 2))
        self.assertEqual(inv[-2], ("inventory", self.sku, 2))

    def test_03_shopify_webhook_verified_before_parsing(self):
        app = App(ctx=self.ctx, adapters=self.adapters, secrets=SECRETS)
        order = {"id": 555, "line_items": [{"id": 1, "sku": self.sku, "quantity": 1}]}
        raw = json.dumps(order).encode()
        bad = handle(app, api_event("POST", "/webhooks/shopify", raw=raw,
                                    headers={"x-shopify-hmac-sha256": "nope", "x-shopify-topic": "orders/create"}))
        self.assertEqual(bad["statusCode"], 401)
        self.assertEqual(ledger.balance(self.ctx, self.sku), 3)
        sig = base64.b64encode(hmac.new(SHOPIFY_SECRET.encode(), raw, hashlib.sha256).digest()).decode()
        ok = handle(app, api_event("POST", "/webhooks/shopify", raw=raw,
                                   headers={"x-shopify-hmac-sha256": sig, "x-shopify-topic": "orders/create"}))
        self.assertEqual(ok["statusCode"], 200)
        self.assertEqual(ledger.balance(self.ctx, self.sku), 2)

    def test_04_webhook_delivered_twice_decrements_once(self):
        order = ChannelOrder("shopify", "555", [OrderLine("1", self.sku, 1)])
        ingest_order(self.ctx, order)
        r = ingest_order(self.ctx, order)
        self.assertEqual(r["duplicate"], 1)
        self.assertEqual(ledger.balance(self.ctx, self.sku), 2)

    def test_05_unknown_sku_alerts_without_failing_order(self):
        order = ChannelOrder("etsy", "77", [OrderLine("a", "ZZ-9999", 1), OrderLine("b", self.sku, 1),
                                           OrderLine("c", None, 1)])
        r = ingest_order(self.ctx, order)
        self.assertEqual((r["applied"], r["unmapped"]), (1, 2))
        kinds = [a["kind"] for a in alerts.list_alerts(self.ctx)]
        self.assertEqual(kinds.count("UNMAPPED_SKU"), 2)

    def test_oversell_alerts_and_never_goes_negative(self):
        r = ingest_order(self.ctx, ChannelOrder("etsy", "78", [OrderLine("a", self.sku, 5)]))
        self.assertEqual(r["oversold"], 1)
        self.assertEqual(ledger.balance(self.ctx, self.sku), 3)
        self.assertEqual(alerts.list_alerts(self.ctx)[0]["kind"], "OVERSELL")

    def test_06_channel_error_raises_for_retry(self):
        self.adapters["meta"] = FakeAdapter("meta", fail=True)
        link_listing(self.ctx, self.sku, "meta", self.adapters)
        link_listing(self.ctx, self.sku, "shopify", self.adapters)
        job = {"type": "push_stock", "tenant": "aalora", "sku": self.sku}
        with self.assertRaises(ChannelError):
            process_job(self.ctx, job, self.adapters)
        self.assertIn(("inventory", self.sku, 3), self.shopify.calls)  # healthy channel still updated

    def test_etsy_polling_ingests_and_advances_cursor(self):
        self.etsy.orders = [ChannelOrder("etsy", "900", [OrderLine("t1", self.sku, 1)])]
        r = poll_channel(self.ctx, self.etsy)
        self.assertEqual(r["applied"], 1)
        r2 = poll_channel(self.ctx, self.etsy)  # same orders returned again
        self.assertEqual(r2["duplicate"], 1)
        self.assertEqual(ledger.balance(self.ctx, self.sku), 2)


if __name__ == "__main__":
    unittest.main()
