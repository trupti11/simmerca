"""Shopify Admin GraphQL adapter. Also carries Aalora storefront checkout (headless).

Required custom-app scopes: read_products, write_products, read_inventory, write_inventory,
read_orders, read_locations.

VERIFY against Shopify's current Admin API docs before go-live: the API version below, and the
`inventorySetQuantities` input shape. Shopify replaced the boolean `ignoreCompareQuantity` with a per-quantity
`changeFromQuantity` (null = skip the compare check) in 2026-era versions; this adapter sends the shape that
matches the configured version (cutover constant below). Shopify versions are supported ~12 months, so
bump `shopify_api_version` in the secret each year. First loop task: contract test on a development store.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from ..errors import ChannelError
from .base import ChannelOrder, OrderLine, cents_to_decimal
from .http import HttpClient

DEFAULT_API_VERSION = "2026-07"
CHANGE_FROM_QUANTITY_SINCE = "2026-04"  # VERIFY in the contract test

Q_VARIANT_BY_SKU = """
query($q: String!) {
  productVariants(first: 5, query: $q) {
    nodes { id sku product { id } inventoryItem { id } }
  }
}"""

M_SET_QTY = """
mutation($input: InventorySetQuantitiesInput!) {
  inventorySetQuantities(input: $input) { userErrors { field message } }
}"""

M_SET_PRICE = """
mutation($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) { userErrors { field message } }
}"""


class ShopifyChannel:
    name = "shopify"

    def __init__(self, shop_domain: str, access_token: str, location_id: str, http: HttpClient | None = None,
                 api_version: str = DEFAULT_API_VERSION):
        self.api_version = api_version
        self.endpoint = f"https://{shop_domain}/admin/api/{api_version}/graphql.json"
        self.token = access_token
        self.location_id = location_id
        self.http = http or HttpClient()

    def _gql(self, query: str, variables: dict) -> dict:
        resp = self.http.request("POST", self.endpoint, headers={"X-Shopify-Access-Token": self.token},
                                 json_body={"query": query, "variables": variables})
        data = resp.json() or {}
        if data.get("errors"):
            raise ChannelError(f"shopify graphql errors: {str(data['errors'])[:300]}")
        return data.get("data") or {}

    @staticmethod
    def _user_errors(payload: dict, key: str) -> None:
        errs = (payload.get(key) or {}).get("userErrors") or []
        if errs:
            raise ChannelError(f"shopify {key}: {errs}")

    def discover_listing(self, sku: str) -> dict | None:
        data = self._gql(Q_VARIANT_BY_SKU, {"q": f"sku:'{sku}'"})
        for node in (data.get("productVariants") or {}).get("nodes", []):
            if node.get("sku") == sku:
                return {
                    "variant_id": node["id"],
                    "product_id": node["product"]["id"],
                    "inventory_item_id": node["inventoryItem"]["id"],
                    "location_id": self.location_id,
                }
        return None

    def push_inventory(self, listing: dict, qty: int) -> None:
        ext = listing["external"]
        quantity = {
            "inventoryItemId": ext["inventory_item_id"],
            "locationId": ext.get("location_id") or self.location_id,
            "quantity": int(qty),
        }
        payload = {"name": "available", "reason": "correction", "quantities": [quantity]}
        if self.api_version >= CHANGE_FROM_QUANTITY_SINCE:
            quantity["changeFromQuantity"] = None  # absolute set, no compare (we push absolute values)
        else:
            payload["ignoreCompareQuantity"] = True
        data = self._gql(M_SET_QTY, {"input": payload})
        self._user_errors(data, "inventorySetQuantities")

    def push_price(self, listing: dict, price_cents: int, currency: str) -> None:
        ext = listing["external"]
        data = self._gql(M_SET_PRICE, {
            "productId": ext["product_id"],
            "variants": [{"id": ext["variant_id"], "price": cents_to_decimal(price_cents)}],
        })
        self._user_errors(data, "productVariantsBulkUpdate")

    def fetch_orders(self, since_iso: str) -> tuple[list[ChannelOrder], str]:
        return [], since_iso  # orders arrive via webhook


def verify_webhook(body: bytes, hmac_header: str | None, secret: str) -> bool:
    """X-Shopify-Hmac-Sha256 = base64(HMAC-SHA256(raw body, app secret))."""
    if not hmac_header or not secret:
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), hmac_header)


def parse_order_webhook(payload: dict) -> ChannelOrder:
    """orders/create payload -> ChannelOrder. Lines without a sku are kept with sku=None (alerted later)."""
    lines = [
        OrderLine(line_id=str(li.get("id")), sku=(li.get("sku") or None), qty=int(li.get("quantity", 0)))
        for li in payload.get("line_items", [])
        if int(li.get("quantity", 0)) > 0
    ]
    return ChannelOrder(channel="shopify", order_id=str(payload["id"]), lines=lines,
                        created_at=payload.get("created_at"))
