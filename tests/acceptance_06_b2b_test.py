"""Spec 06 acceptance tests. OWNER-CONTROLLED: agents may not edit this file."""

import unittest

from helpers import make_ctx, seed

from simmerca import b2b, ledger
from simmerca.errors import Forbidden, InsufficientStock, NotFound, ValidationError


class B2BAcceptance(unittest.TestCase):
    def setUp(self):
        self.ctx = make_ctx()
        _, self.p = seed(self.ctx, stock=6)
        _, self.mto = seed(self.ctx, stock=0, made_to_order=True)
        self.sku = self.p["sku"]
        b2b.upsert_buyer_profile(self.ctx, "buyer-a", {"company": "Boutique A"})
        b2b.upsert_buyer_profile(self.ctx, "buyer-b", {"company": "Boutique B"})

    def approve(self, buyer_id):
        b2b.set_buyer_status(self.ctx, buyer_id, "approved", "admin:t")

    def test_01_unapproved_buyer_cannot_quote(self):
        with self.assertRaises(Forbidden):
            b2b.create_quote(self.ctx, "buyer-a", [{"sku": self.sku, "qty": 1}])

    def test_02_quote_priced_with_tiers_and_availability(self):
        self.approve("buyer-a")
        q = b2b.create_quote(self.ctx, "buyer-a", [{"sku": self.sku, "qty": 5}, {"sku": self.mto["sku"], "qty": 1}])
        self.assertEqual(q["lines"][0]["unit_price_cents"], 40500)
        self.assertEqual(q["lines"][0]["availability"]["status"], "in_stock")
        self.assertEqual(q["lines"][1]["availability"]["status"], "made_to_order")
        self.assertEqual(q["status"], "requested")

    def test_03_override_never_below_floor(self):
        self.approve("buyer-a")
        q = b2b.create_quote(self.ctx, "buyer-a", [{"sku": self.sku, "qty": 1}])
        with self.assertRaises(ValidationError):
            b2b.approve_quote(self.ctx, q["id"], "admin:t", price_overrides={self.sku: 100})

    def test_04_approval_reserves_stock_or_writes_nothing(self):
        self.approve("buyer-a")
        q = b2b.create_quote(self.ctx, "buyer-a", [{"sku": self.sku, "qty": 4}])
        b2b.approve_quote(self.ctx, q["id"], "admin:t")
        self.assertEqual(ledger.balance(self.ctx, self.sku), 2)
        q2 = b2b.create_quote(self.ctx, "buyer-a", [{"sku": self.mto["sku"], "qty": 1}, {"sku": self.sku, "qty": 3}])
        with self.assertRaises(InsufficientStock):
            b2b.approve_quote(self.ctx, q2["id"], "admin:t")
        self.assertEqual(ledger.balance(self.ctx, self.sku), 2)
        self.assertEqual(b2b.get_quote(self.ctx, q2["id"])["status"], "requested")
        self.assertTrue(ledger.reconcile(self.ctx, self.sku)["ok"])

    def test_05_buyers_see_only_their_quotes(self):
        self.approve("buyer-a")
        q = b2b.create_quote(self.ctx, "buyer-a", [{"sku": self.sku, "qty": 1}])
        with self.assertRaises(NotFound):
            b2b.get_quote(self.ctx, q["id"], buyer_id="buyer-b")
        self.assertEqual(b2b.list_quotes(self.ctx, buyer_id="buyer-b"), [])


if __name__ == "__main__":
    unittest.main()
