"""Spec 03 acceptance tests. OWNER-CONTROLLED: agents may not edit this file."""

import json
import unittest

from helpers import make_ctx, seed

from simmerca import products
from simmerca.errors import ValidationError
from simmerca.intelligence.extractor import approve_attributes, propose_attributes, review_queue
from simmerca.views import public_product


class FakeModel:
    def __init__(self, output):
        self.output = output

    def describe(self, images, prompt):
        assert images and prompt
        return self.output if isinstance(self.output, str) else json.dumps(self.output)


GOOD = {
    "fabric": {"value": "Kanchipuram silk", "confidence": 0.92},
    "weave": {"value": "brocade", "confidence": 0.85},
    "primary_color": {"value": "crimson", "confidence": 0.95},
    "secondary_colors": {"value": ["golden", "green"], "confidence": 0.8},
    "motif": {"value": "temple", "confidence": 0.62},
    "border": {"value": "korvai", "confidence": 0.81},
    "zari": {"value": True, "confidence": 0.9},
}


class IntelligenceAcceptance(unittest.TestCase):
    def setUp(self):
        self.ctx = make_ctx()
        _, p = seed(self.ctx, stock=1, status="draft")
        self.sku = p["sku"]
        self.ctx.blobs.put_bytes("aalora/products/x/1.jpg", b"\xff\xd8fakejpeg", "image/jpeg")
        products.update_product(self.ctx, self.sku, {"images": ["aalora/products/x/1.jpg"]}, "t")

    def test_01_02_proposes_normalized_values_with_confidence(self):
        p = propose_attributes(self.ctx, self.sku, FakeModel(GOOD))
        prop = p["proposed_attributes"]
        self.assertEqual(prop["fabric"], {"value": "Kanchi Silk", "confidence": 0.92})
        self.assertEqual(prop["primary_color"]["value"], "red")
        self.assertEqual(prop["secondary_colors"]["value"], ["gold", "green"])
        self.assertIs(prop["zari"]["value"], True)

    def test_03_invalid_output_never_raises(self):
        for bad in ("not json", '{"fabric": {"value": "Polyester", "confidence": 0.99}}', "[]", ""):
            p = propose_attributes(self.ctx, self.sku, FakeModel(bad))
            self.assertIsNone(p["proposed_attributes"]["fabric"]["value"])
            self.assertEqual(p["proposed_attributes"]["fabric"]["confidence"], 0.0)

    def test_04_never_auto_approves_and_proposals_not_public(self):
        before = dict(products.get_product(self.ctx, self.sku)["attributes"])
        p = propose_attributes(self.ctx, self.sku, FakeModel(GOOD))
        self.assertEqual(p["attribute_status"], "pending_review")
        self.assertEqual(p["attributes"], before)
        self.assertNotIn("motif", public_product(self.ctx, p)["attributes"])
        with self.assertRaises(ValidationError):
            products.update_product(self.ctx, self.sku, {"status": "active"}, "t")

    def test_05_low_confidence_fields_flagged_and_sorted_first(self):
        propose_attributes(self.ctx, self.sku, FakeModel(GOOD))
        _, other = seed(self.ctx, stock=0, status="draft")
        self.ctx.blobs.put_bytes("k2.jpg", b"x", "image/jpeg")
        products.update_product(self.ctx, other["sku"], {"images": ["k2.jpg"]}, "t")
        weak = {k: {"value": v["value"], "confidence": 0.3} for k, v in GOOD.items()}
        propose_attributes(self.ctx, other["sku"], FakeModel(weak))
        queue = review_queue(self.ctx)
        self.assertEqual(queue[0]["sku"], other["sku"])
        self.assertIn("motif", products.get_product(self.ctx, self.sku)["needs_review_fields"])

    def test_06_approved_attributes_become_public_with_human_edits(self):
        propose_attributes(self.ctx, self.sku, FakeModel(GOOD))
        p = approve_attributes(self.ctx, self.sku, "admin:t", edits={"motif": "peacock"})
        self.assertEqual(p["attribute_status"], "approved")
        public = public_product(self.ctx, p)["attributes"]
        self.assertEqual(public["motif"], "peacock")
        self.assertEqual(public["fabric"], "Kanchi Silk")
        with self.assertRaises(Exception):
            approve_attributes(self.ctx, self.sku, "admin:t")  # nothing pending now

    def test_edits_are_validated(self):
        propose_attributes(self.ctx, self.sku, FakeModel(GOOD))
        with self.assertRaises(ValidationError):
            approve_attributes(self.ctx, self.sku, "admin:t", edits={"fabric": "Polyester"})


if __name__ == "__main__":
    unittest.main()
