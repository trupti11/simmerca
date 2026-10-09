"""Spec 02 acceptance tests. OWNER-CONTROLLED: agents may not edit this file."""

import unittest

from helpers import PATH_TOKEN, SECRETS, make_ctx, seed, sign, twilio_params, whatsapp_url

from simmerca import ledger, products, suppliers
from simmerca.whatsapp.flow import handle_inbound


class WhatsAppAcceptance(unittest.TestCase):
    def setUp(self):
        self.ctx = make_ctx()
        self.sup, self.p = seed(self.ctx, stock=1)
        self.sku = self.p["sku"]
        self.n = 0

    def send(self, body, frm="whatsapp:+919845012345", media=None, token=PATH_TOKEN, signature=None, sid=None):
        self.n += 1
        params = twilio_params(body, sid or f"SM{self.n:032d}", frm, media)
        sig = signature if signature is not None else sign(params)
        return handle_inbound(self.ctx, path_token=token, url=whatsapp_url(token), params=params, signature=sig,
                              secrets=SECRETS)

    def test_01_02_bad_token_or_signature_is_403_and_writes_nothing(self):
        status, _ = self.send(f"ADD {self.sku} 2", token="wrong")
        self.assertEqual(status, 403)
        status, _ = self.send(f"ADD {self.sku} 2", signature="bad")
        self.assertEqual(status, 403)
        self.assertEqual(ledger.balance(self.ctx, self.sku), 1)

    def test_01_duplicate_message_sid_is_ignored(self):
        self.send(f"ADD {self.sku} 2", sid="SMdup")
        status, body = self.send("YES", sid="SMdup")
        self.assertEqual(status, 200)
        self.assertNotIn("Done", body)

    def test_02_unknown_sender_gets_polite_reply_and_no_write(self):
        status, body = self.send(f"ADD {self.sku} 2", frm="whatsapp:+15550001111")
        self.assertEqual(status, 200)
        self.assertIn("not registered", body)
        self.assertEqual(ledger.balance(self.ctx, self.sku), 1)

    def test_05_07_no_write_without_confirmation_then_confirmed_once(self):
        _, body = self.send(f"ADD {self.sku} 2")
        self.assertEqual(ledger.balance(self.ctx, self.sku), 1)
        self.assertIn(self.sku, body)
        _, body = self.send("हाँ")
        self.assertEqual(ledger.balance(self.ctx, self.sku), 3)
        self.assertIn("3", body)
        _, body = self.send("YES")  # nothing pending any more
        self.assertEqual(ledger.balance(self.ctx, self.sku), 3)

    def test_04_hindi_with_devanagari_digits(self):
        self.send(f"{self.sku} २ जोड़ें")
        self.send("हाँ")
        self.assertEqual(ledger.balance(self.ctx, self.sku), 3)

    def test_04_sold_shorthand(self):
        self.send(f"-1 {self.sku}")
        self.send("ok")
        self.assertEqual(ledger.balance(self.ctx, self.sku), 0)

    def test_05_confirmation_expires_after_15_minutes(self):
        self.send(f"ADD {self.sku} 2")
        self.ctx.clock.advance(minutes=16)
        _, body = self.send("YES")
        self.assertEqual(ledger.balance(self.ctx, self.sku), 1)

    def test_05_cancel(self):
        self.send(f"ADD {self.sku} 2")
        self.send("NO")
        self.send("YES")
        self.assertEqual(ledger.balance(self.ctx, self.sku), 1)

    def test_06_cannot_change_another_weavers_stock(self):
        suppliers.create_supplier(self.ctx, {"name": "Other", "phone": "+919800000000"})
        _, body = self.send(f"ADD {self.sku} 2", frm="whatsapp:+919800000000")
        self.assertNotIn("Reply YES", body)
        self.send("YES", frm="whatsapp:+919800000000")
        self.assertEqual(ledger.balance(self.ctx, self.sku), 1)

    def test_selling_more_than_stock_is_refused_before_confirm(self):
        _, body = self.send(f"SOLD {self.sku} 5")
        self.send("YES")
        self.assertEqual(ledger.balance(self.ctx, self.sku), 1)

    def test_08_photo_creates_draft_and_intelligence_job(self):
        _, body = self.send("new red tussar", media=["https://api.twilio.com/2010-04-01/Accounts/AC1/Messages/MM1/Media/ME1"])
        jobs = self.ctx.intelligence_queue.drain()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["type"], "ingest_media")
        new = products.get_product(self.ctx, jobs[0]["sku"])
        self.assertEqual(new["status"], "draft")
        self.assertEqual(new["supplier_id"], self.sup["id"])
        self.assertIn(new["sku"], body)

    def test_query(self):
        _, body = self.send(f"{self.sku} ?")
        self.assertIn("1", body)


if __name__ == "__main__":
    unittest.main()
