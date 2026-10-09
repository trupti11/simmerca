"""The router and openapi.yaml must describe the same API (catches contract drift for the frontend)."""

import os
import re
import unittest

import helpers  # noqa: F401

from simmerca.api.router import ROUTES

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

SPEC = os.path.join(os.path.dirname(__file__), "..", "openapi.yaml")


@unittest.skipIf(yaml is None, "pyyaml not installed")
class Contract(unittest.TestCase):
    def test_router_and_openapi_match(self):
        with open(SPEC, encoding="utf-8") as f:
            spec = yaml.safe_load(f)
        documented = {(m.upper(), p) for p, ops in spec["paths"].items() for m in ops}
        implemented = set()
        for method, regex, _group, _fn in ROUTES:
            path = re.sub(r"\(\?P<(\w+)>\[\^/\]\+\)", r"{\1}", regex.pattern.strip("^$"))
            implemented.add((method, path))
        self.assertEqual(sorted(implemented - documented), [], "implemented but not in openapi.yaml")
        self.assertEqual(sorted(documented - implemented), [], "in openapi.yaml but not implemented")


if __name__ == "__main__":
    unittest.main()
