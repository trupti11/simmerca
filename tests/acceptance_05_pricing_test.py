"""Spec 05 acceptance tests. OWNER-CONTROLLED: agents may not edit this file."""

import unittest

from helpers import make_ctx, seed

from simmerca import pricing, products
from simmerca.errors import ValidationError


class PricingAcceptance(unittest.TestCase):
    def setUp(self):
        self.ctx = make_ctx()
        _, self.p = seed(self.ctx, stock=20)
        self.sku = self.p["sku"]

    def test_01_rule_price_rounds_up_whole_unit(self):
        # 200.00 * 1.60 / 0.90 = 355.555... -> 356.00
        self.assertEqual(pricing.rule_price(20000, 60, 10), 35600)
        self.assertEqual(pricing.rule_price(10000, 0, 0), 10000)

    def test_02_floor(self):
        self.assertEqual(pricing.floor_price(20000, 25), 25000)

    def test_03_tiers_and_floor_clamp(self):
        self.assertEqual(pricing.unit_price(self.ctx, self.p, 1, b2b=True)["unit_price_cents"], 45000)
        self.assertEqual(pricing.unit_price(self.ctx, self.p, 5, b2b=True)["unit_price_cents"], 40500)
        self.assertEqual(pricing.unit_price(self.ctx, self.p, 10, b2b=True)["unit_price_cents"], 36900)
        self.assertEqual(pricing.unit_price(self.ctx, self.p, 10, b2b=False)["unit_price_cents"], 45000)
        cheap = dict(self.p, price_cents=26000)  # 18% off would be 21320 < floor 25000
        r = pricing.unit_price(self.ctx, cheap, 10, b2b=True)
        self.assertEqual(r["unit_price_cents"], 25000)
        self.assertTrue(r["clamped_to_floor"])

    def test_04_suggestion_uses_comparables_and_is_clamped(self):
        for price in (40000, 50000, 60000):
            pricing.add_comparable(self.ctx, {"fabric": "Kanjivaram", "price_cents": price, "currency": "USD"}, "t")
        s = pricing.suggest_price(self.ctx, self.sku)
        self.assertEqual(s["suggested_price_cents"], 50000)
        self.assertEqual(s["method"], "median_of_comparables")
        pricing.add_comparable(self.ctx, {"fabric": "Kanjivaram", "price_cents": 1000, "currency": "USD"}, "t")
        pricing.add_comparable(self.ctx, {"fabric": "Kanjivaram", "price_cents": 1000, "currency": "USD"}, "t")
        pricing.add_comparable(self.ctx, {"fabric": "Kanjivaram", "price_cents": 1000, "currency": "USD"}, "t")
        pricing.add_comparable(self.ctx, {"fabric": "Kanjivaram", "price_cents": 1000, "currency": "USD"}, "t")
        s = pricing.suggest_price(self.ctx, self.sku)
        self.assertGreaterEqual(s["suggested_price_cents"], s["floor_price_cents"])

    def test_05_price_changes_only_through_approved_proposals(self):
        with self.assertRaises(ValidationError):
            products.update_product(self.ctx, self.sku, {"price_cents": 1}, "t")
        with self.assertRaises(ValidationError):
            pricing.propose_price(self.ctx, self.sku, 1000, "t")  # below floor
        prop = pricing.propose_price(self.ctx, self.sku, 48000, "admin:t", "festival")
        self.assertEqual(products.get_product(self.ctx, self.sku)["price_cents"], 45000)
        self.assertEqual(self.ctx.queue.drain(), [])
        pricing.approve_proposal(self.ctx, prop["id"], "admin:t")
        self.assertEqual(products.get_product(self.ctx, self.sku)["price_cents"], 48000)
        self.assertEqual(self.ctx.queue.drain(), [{"type": "push_price", "tenant": "aalora", "sku": self.sku}])


if __name__ == "__main__":
    unittest.main()
