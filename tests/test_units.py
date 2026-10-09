"""Unit tests (agents may add to these)."""

import json
import unittest

import helpers  # noqa: F401  (sets sys.path)

from simmerca.channels.base import cents_to_decimal
from simmerca.channels.etsy import EtsyChannel, EtsyTokens
from simmerca.channels.http import HttpClient, HttpResponse
from simmerca.channels.meta import MetaCatalogChannel
from simmerca.channels.shopify import ShopifyChannel, parse_order_webhook
from simmerca.errors import ChannelError
from simmerca.vocab import normalize_color, normalize_fabric
from simmerca.whatsapp.parser import parse, parse_rules, validate_llm_output
from simmerca.whatsapp.security import compute_twilio_signature


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, method, url, headers, body, timeout):
        self.requests.append({"method": method, "url": url, "headers": headers,
                              "body": json.loads(body) if body and headers.get("Content-Type") == "application/json"
                              else body})
        status, payload = self.responses.pop(0)
        return HttpResponse(status, json.dumps(payload).encode() if payload is not None else b"", {})


def client(responses):
    t = FakeTransport(responses)
    return HttpClient(transport=t, sleep=lambda s: None), t


class TwilioSignature(unittest.TestCase):
    def test_matches_twilio_documented_example(self):
        params = {"CallSid": "CA1234567890ABCDE", "Caller": "+12349013030", "Digits": "1234",
                  "From": "+12349013030", "To": "+18005551212"}
        sig = compute_twilio_signature("https://mycompany.com/myapp.php?foo=1&bar=2", params, "12345")
        self.assertEqual(sig, "0/KCTR6DLpKmkAf8muzZqo1nDgQ=")


class Parser(unittest.TestCase):
    def test_examples(self):
        cases = {
            "ADD KJ-114 2": ("add", "KJ-0114", 2),
            "kj 114 sold 1": ("sold", "KJ-0114", 1),
            "+3 BN-0007": ("add", "BN-0007", 3),
            "TS-0002 -1": ("sold", "TS-0002", 1),
            "KJ-0114 3 left": ("set", "KJ-0114", 3),
            "KJ-0114 ?": ("query", "KJ-0114", None),
            "KJ-0114 kitne hai": ("query", "KJ-0114", None),
            "KJ-0114 दो बिकी": ("sold", "KJ-0114", 2),
            "KJ-0114 ৩ বিক্রি": ("sold", "KJ-0114", 3),
            "KJ-0114 ௨ சேர்": ("add", "KJ-0114", 2),
            "KJ-0114 5": ("unknown", "KJ-0114", 5),
        }
        for text, (intent, sku, qty) in cases.items():
            p = parse_rules(text)
            self.assertEqual((p.intent, p.sku, p.qty), (intent, sku, qty), text)

    def test_confirm_cancel_help(self):
        for t in ("YES", "हाँ", "ஆம்", "ok.", "👍"):
            self.assertEqual(parse_rules(t).intent, "confirm", t)
        for t in ("no", "नहीं", "ਨਾ" if False else "না", "cancel"):
            self.assertEqual(parse_rules(t).intent, "cancel", t)
        self.assertEqual(parse_rules("help").intent, "help")
        self.assertEqual(parse_rules("anything", has_media=True).intent, "new_product")

    def test_does_not_mistake_words_for_codes(self):
        self.assertEqual(parse_rules("address 12").intent, "unknown")
        self.assertIsNone(parse_rules("NO 2").sku)

    def test_llm_fallback_validated(self):
        class Llm:
            def __init__(self, out):
                self.out = out

            def complete_json(self, system, user):
                return self.out

        good = Llm('{"intent":"add","sku":"kj-114","qty":2,"confidence":0.9}')
        p = parse("teen naye aaye hain kanjivaram wale kj114 me, do", llm=good)
        self.assertIn(p.intent, {"add", "unknown"})
        self.assertEqual(parse("random words", llm=Llm('{"intent":"add","sku":"KJ-0001","qty":2,"confidence":0.95}')).source,
                         "llm")
        self.assertEqual(parse("random words", llm=Llm("garbage")).intent, "unknown")
        self.assertEqual(parse("random words", llm=Llm('{"intent":"delete_all"}')).intent, "unknown")
        self.assertEqual(parse("random words", llm=Llm('{"intent":"add","sku":"KJ-1","qty":2,"confidence":0.4}')).intent,
                         "unknown")

        class Boom:
            def complete_json(self, s, u):
                raise RuntimeError("bedrock down")

        self.assertEqual(parse("random words", llm=Boom()).intent, "unknown")

    def test_validate_llm_rejects_bad_qty(self):
        self.assertEqual(validate_llm_output('{"intent":"add","sku":"KJ-1","qty":-5,"confidence":1}').intent, "unknown")
        self.assertEqual(validate_llm_output('{"intent":"add","sku":"KJ-1","qty":true,"confidence":1}').intent, "unknown")


class Vocab(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_fabric("Kanchipuram Silk"), "Kanjivaram")
        self.assertEqual(normalize_fabric("pure sico saree"), "Silk Cotton")
        self.assertIsNone(normalize_fabric("polyester"))
        self.assertEqual(normalize_color("Rani Pink"), "magenta")
        self.assertEqual(normalize_color("deep maroon"), "maroon")
        self.assertEqual(cents_to_decimal(45000), "450.00")
        self.assertEqual(cents_to_decimal(5), "0.05")


class Http(unittest.TestCase):
    def test_retries_then_succeeds(self):
        c, t = client([(429, None), (503, None), (200, {"ok": True})])
        self.assertEqual(c.request("GET", "https://x.test/a").json(), {"ok": True})
        self.assertEqual(len(t.requests), 3)

    def test_4xx_fails_fast(self):
        c, t = client([(400, {"error": "bad"})])
        with self.assertRaises(ChannelError):
            c.request("GET", "https://x.test/a")
        self.assertEqual(len(t.requests), 1)

    def test_gives_up(self):
        c, _ = client([(500, None)] * 5)
        with self.assertRaises(ChannelError):
            c.request("GET", "https://x.test/a?token=secret")


LISTING = {"sku": "KJ-0001"}


class Shopify(unittest.TestCase):
    def test_discover_and_push(self):
        c, t = client([
            (200, {"data": {"productVariants": {"nodes": [
                {"id": "gid://shopify/ProductVariant/1", "sku": "KJ-0001", "product": {"id": "gid://shopify/Product/9"},
                 "inventoryItem": {"id": "gid://shopify/InventoryItem/5"}}]}}}),
            (200, {"data": {"inventorySetQuantities": {"userErrors": []}}}),
            (200, {"data": {"productVariantsBulkUpdate": {"userErrors": []}}}),
        ])
        s = ShopifyChannel("aalora.myshopify.com", "tok", "gid://shopify/Location/1", c)
        ext = s.discover_listing("KJ-0001")
        self.assertEqual(ext["inventory_item_id"], "gid://shopify/InventoryItem/5")
        s.push_inventory({"sku": "KJ-0001", "external": ext}, 3)
        inp = t.requests[1]["body"]["variables"]["input"]
        self.assertEqual(inp["quantities"][0], {"inventoryItemId": "gid://shopify/InventoryItem/5",
                                                "locationId": "gid://shopify/Location/1", "quantity": 3,
                                                "changeFromQuantity": None})
        self.assertNotIn("ignoreCompareQuantity", inp)
        self.assertEqual(t.requests[1]["headers"]["X-Shopify-Access-Token"], "tok")
        s.push_price({"sku": "KJ-0001", "external": ext}, 45000, "USD")
        self.assertEqual(t.requests[2]["body"]["variables"]["variants"][0]["price"], "450.00")

    def test_older_api_version_uses_ignore_compare(self):
        c, t = client([(200, {"data": {"inventorySetQuantities": {"userErrors": []}}})])
        s = ShopifyChannel("a.myshopify.com", "t", "L", c, api_version="2025-10")
        s.push_inventory({"sku": "X", "external": {"inventory_item_id": "I"}}, 2)
        inp = t.requests[0]["body"]["variables"]["input"]
        self.assertIs(inp["ignoreCompareQuantity"], True)
        self.assertNotIn("changeFromQuantity", inp["quantities"][0])

    def test_user_errors_raise(self):
        c, _ = client([(200, {"data": {"inventorySetQuantities": {"userErrors": [{"message": "nope"}]}}})])
        s = ShopifyChannel("a.myshopify.com", "t", "L", c)
        with self.assertRaises(ChannelError):
            s.push_inventory({"sku": "X", "external": {"inventory_item_id": "I"}}, 1)

    def test_parse_webhook(self):
        o = parse_order_webhook({"id": 1, "line_items": [{"id": 2, "sku": "KJ-0001", "quantity": 2},
                                                         {"id": 3, "sku": "", "quantity": 1}]})
        self.assertEqual([(ln.sku, ln.qty) for ln in o.lines], [("KJ-0001", 2), (None, 1)])


ETSY_INV = {
    "products": [{
        "product_id": 1, "sku": "KJ-0001", "is_deleted": False, "property_values": [],
        "offerings": [{"offering_id": 7, "price": {"amount": 45000, "divisor": 100, "currency_code": "USD"},
                       "quantity": 2, "is_enabled": True, "is_deleted": False}],
    }],
    "price_on_property": [], "quantity_on_property": [], "sku_on_property": [],
}


class Etsy(unittest.TestCase):
    def make(self, responses):
        c, t = client([(200, {"access_token": "AT", "expires_in": 3600, "refresh_token": "RT2"})] + responses)
        saved = {}
        tokens = EtsyTokens("KEY", "RT1", c, save=saved.update, clock=lambda: 1000.0)
        return EtsyChannel("KEY", "SHOP", tokens, c), t, saved

    def test_push_inventory_put_body_and_token_rotation(self):
        e, t, saved = self.make([(200, ETSY_INV), (200, {})])
        e.push_inventory({"sku": "KJ-0001", "external": {"listing_id": "55"}}, 4)
        put = t.requests[2]
        self.assertEqual(put["method"], "PUT")
        offering = put["body"]["products"][0]["offerings"][0]
        self.assertEqual(offering, {"price": 450.0, "quantity": 4, "is_enabled": True})
        self.assertNotIn("product_id", put["body"]["products"][0])
        self.assertEqual(put["headers"]["Authorization"], "Bearer AT")
        self.assertEqual(saved, {"etsy_refresh_token": "RT2"})

    def test_readiness_state_preserved(self):
        inv = json.loads(json.dumps(ETSY_INV))
        inv["products"][0]["offerings"][0]["readiness_state_id"] = 123
        e, t, _ = self.make([(200, inv), (200, {})])
        e.push_inventory({"sku": "KJ-0001", "external": {"listing_id": "55"}}, 1)
        self.assertEqual(t.requests[2]["body"]["products"][0]["offerings"][0]["readiness_state_id"], 123)

    def test_token_reload_after_rotation_elsewhere(self):
        c, t = client([(400, {"error": "invalid_grant"}),
                       (200, {"access_token": "AT2", "expires_in": 3600})])
        tokens = EtsyTokens("KEY", "OLD", c, reload=lambda: "NEW", clock=lambda: 1.0)
        self.assertEqual(tokens.access_token(), "AT2")
        self.assertIn("refresh_token=NEW", t.requests[1]["body"].decode())

    def test_zero_deactivates_listing(self):
        e, t, _ = self.make([(200, ETSY_INV), (200, {})])
        e.push_inventory({"sku": "KJ-0001", "external": {"listing_id": "55"}}, 0)
        self.assertEqual(t.requests[2]["method"], "PATCH")

    def test_missing_sku_raises(self):
        e, _, _ = self.make([(200, ETSY_INV)])
        with self.assertRaises(ChannelError):
            e.push_inventory({"sku": "KJ-0002", "external": {"listing_id": "55"}}, 1)

    def test_fetch_orders(self):
        e, _, _ = self.make([(200, {"count": 1, "results": [{
            "receipt_id": 9, "create_timestamp": 1790000000,
            "transactions": [{"transaction_id": 11, "sku": "KJ-0001", "quantity": 1}]}]})])
        orders, cursor = e.fetch_orders("2026-10-06T00:00:00Z")
        self.assertEqual(orders[0].order_id, "9")
        self.assertEqual(orders[0].lines[0].sku, "KJ-0001")
        self.assertTrue(cursor.endswith("Z"))


class LedgerRaces(unittest.TestCase):
    def test_quote_rollback_on_mid_approval_failure_then_reapprove(self):
        from unittest import mock

        from helpers import make_ctx, seed
        from simmerca import b2b, ledger
        from simmerca.errors import ConcurrentModification

        ctx = make_ctx()
        _, a = seed(ctx, stock=5)
        _, b = seed(ctx, stock=5, fabric="Banarasi")
        b2b.upsert_buyer_profile(ctx, "u", {"company": "X"})
        b2b.set_buyer_status(ctx, "u", "approved", "t")
        q = b2b.create_quote(ctx, "u", [{"sku": a["sku"], "qty": 2}, {"sku": b["sku"], "qty": 2}])
        real = ledger.record_change
        calls = {"n": 0}

        def flaky(*args, **kw):
            calls["n"] += 1
            if calls["n"] == 2:
                raise ConcurrentModification("race")
            return real(*args, **kw)

        with mock.patch("simmerca.b2b.record_change", side_effect=flaky):
            with self.assertRaises(ConcurrentModification):
                b2b.approve_quote(ctx, q["id"], "t")
        self.assertEqual((ledger.balance(ctx, a["sku"]), ledger.balance(ctx, b["sku"])), (5, 5))
        b2b.approve_quote(ctx, q["id"], "t")
        self.assertEqual((ledger.balance(ctx, a["sku"]), ledger.balance(ctx, b["sku"])), (3, 3))
        self.assertTrue(ledger.reconcile(ctx, a["sku"])["ok"])

    def test_oversold_line_not_consumed_after_restock(self):
        from helpers import make_ctx, seed
        from simmerca import ledger
        from simmerca.channels.base import ChannelOrder, OrderLine
        from simmerca.channels.sync import ingest_order

        ctx = make_ctx()
        _, p = seed(ctx, stock=1)
        order = ChannelOrder("etsy", "1", [OrderLine("x", p["sku"], 2)])
        self.assertEqual(ingest_order(ctx, order)["oversold"], 1)
        ledger.record_change(ctx, p["sku"], 5, "RECEIVE", "t", "t", "restock")
        self.assertEqual(ingest_order(ctx, order)["duplicate"], 1)
        self.assertEqual(ledger.balance(ctx, p["sku"]), 6)

    def test_event_audit_fields_reflect_balance(self):
        from helpers import make_ctx, seed
        from simmerca import ledger

        ctx = make_ctx()
        _, p = seed(ctx, stock=3)
        ledger.record_change(ctx, p["sku"], -1, "SALE", "t", "t", "s1")
        ev = ledger.events(ctx, p["sku"])[0]
        self.assertEqual((ev["on_hand_before"], ev["on_hand_after"]), (3, 2))


class Meta(unittest.TestCase):
    def test_items_batch(self):
        c, t = client([(200, {"handles": ["h"]}), (200, {"handles": ["h"]})])
        m = MetaCatalogChannel("CAT", "TOK", c)
        m.push_inventory({"sku": "KJ-0001", "external": {"retailer_id": "KJ-0001"}}, 0)
        data = t.requests[0]["body"]["requests"][0]["data"]
        self.assertEqual(data, {"id": "KJ-0001", "inventory": 0, "availability": "out of stock"})
        m.push_price({"sku": "KJ-0001", "external": {"retailer_id": "KJ-0001"}}, 45000, "USD")
        self.assertEqual(t.requests[1]["body"]["requests"][0]["data"]["price"], "450.00 USD")

    def test_item_level_errors_raise(self):
        c, _ = client([(200, {"handles": ["h"], "validation_status": [
            {"retailer_id": "KJ-0001", "errors": [{"message": "item not found"}]}]})])
        m = MetaCatalogChannel("CAT", "TOK", c)
        with self.assertRaises(ChannelError):
            m.push_inventory({"sku": "KJ-0001", "external": {"retailer_id": "KJ-0001"}}, 1)


if __name__ == "__main__":
    unittest.main()
