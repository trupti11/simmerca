"""Spec 01 acceptance tests. OWNER-CONTROLLED: agents may not edit this file."""

import unittest

from helpers import make_ctx, seed

from simmerca import ledger, products
from simmerca.errors import InsufficientStock, ValidationError
from simmerca.views import public_product


class LedgerAcceptance(unittest.TestCase):
    def setUp(self):
        self.ctx = make_ctx()
        self.sup, self.p = seed(self.ctx, stock=3)
        self.sku = self.p["sku"]

    def test_01_every_change_is_an_event_with_full_context(self):
        ledger.record_change(self.ctx, self.sku, -1, "SALE", "shopify", "channel:shopify", "k1", ref="order-9")
        ev = ledger.events(self.ctx, self.sku)[0]
        for field in ("sku", "delta", "reason", "source", "actor", "ref", "idem_key", "at"):
            self.assertIn(field, ev)
        self.assertEqual((ev["delta"], ev["reason"], ev["ref"]), (-1, "SALE", "order-9"))

    def test_02_stock_never_goes_negative(self):
        with self.assertRaises(InsufficientStock):
            ledger.record_change(self.ctx, self.sku, -4, "SALE", "t", "t", "k-neg")
        self.assertEqual(ledger.balance(self.ctx, self.sku), 3)
        self.assertTrue(ledger.reconcile(self.ctx, self.sku)["ok"])

    def test_03_same_idempotency_key_applies_once(self):
        a = ledger.record_change(self.ctx, self.sku, -1, "SALE", "t", "t", "dup")
        b = ledger.record_change(self.ctx, self.sku, -1, "SALE", "t", "t", "dup")
        self.assertTrue(a["applied"])
        self.assertFalse(b["applied"])
        self.assertEqual(a["on_hand"], b["on_hand"])
        self.assertEqual(ledger.balance(self.ctx, self.sku), 2)

    def test_04_reconcile_detects_mismatch(self):
        self.assertTrue(ledger.reconcile(self.ctx, self.sku)["ok"])
        from simmerca import keys

        bal = self.ctx.store.get(*keys.balance("aalora", self.sku))
        bal["on_hand"] = 99
        self.ctx.store.put(bal)
        report = ledger.reconcile(self.ctx, self.sku)
        self.assertFalse(report["ok"])
        self.assertEqual(report["ledger_sum"], 3)

    def test_05_made_to_order_never_in_stock_and_sale_creates_production_order(self):
        _, mto = seed(self.ctx, stock=0, made_to_order=True)
        view = public_product(self.ctx, mto)
        self.assertEqual(view["availability"]["status"], "made_to_order")
        self.assertEqual(view["availability"]["lead_time_days"], 21)
        self.assertEqual(products.available_qty(self.ctx, mto), 2)  # mto_capacity
        r = ledger.record_change(self.ctx, mto["sku"], -1, "SALE", "shopify", "t", "mto-1")
        self.assertIsNotNone(r["production_order_id"])
        self.assertEqual(ledger.balance(self.ctx, mto["sku"]), 0)

    def test_06_each_applied_change_enqueues_one_push(self):
        ledger.record_change(self.ctx, self.sku, 2, "RECEIVE", "t", "t", "q1")
        ledger.record_change(self.ctx, self.sku, 2, "RECEIVE", "t", "t", "q1")  # duplicate: no new job
        jobs = self.ctx.queue.drain()
        self.assertEqual(jobs, [{"type": "push_stock", "tenant": "aalora", "sku": self.sku}])

    def test_07_short_code_sku_from_fabric(self):
        self.assertRegex(self.sku, r"^KJ-\d{4}$")
        p2 = products.create_product(self.ctx, {"fabric": "Banarasi"}, "t")
        self.assertRegex(p2["sku"], r"^BN-0001$")

    def test_08_sign_rules_enforced(self):
        with self.assertRaises(ValidationError):
            ledger.record_change(self.ctx, self.sku, 1, "SALE", "t", "t", "bad-sign")
        with self.assertRaises(ValidationError):
            ledger.record_change(self.ctx, self.sku, -1, "RECEIVE", "t", "t", "bad-sign2")

    def test_09_set_count_corrects_to_absolute(self):
        r = ledger.set_count(self.ctx, self.sku, 1, "admin", "t", "count-1")
        self.assertEqual(r["on_hand"], 1)
        self.assertTrue(ledger.reconcile(self.ctx, self.sku)["ok"])


if __name__ == "__main__":
    unittest.main()
