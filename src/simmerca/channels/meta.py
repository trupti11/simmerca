"""Meta (Facebook/Instagram) catalog adapter. Catalog sync only: inventory, price, availability.

Convention: each catalog item's retailer id (`id` in feeds) == our sku.
Needs a system-user token with catalog_management. VERIFY the Graph API version and items_batch field
formats against current Meta docs before go-live (in particular whether `inventory` or
`quantity_to_sell_on_facebook` is the current quantity field); contract-test against a test catalog.
"""

from __future__ import annotations

from ..errors import ChannelError
from .base import ChannelOrder, cents_to_decimal
from .http import HttpClient

DEFAULT_GRAPH_VERSION = "v23.0"


class MetaCatalogChannel:
    name = "meta"

    def __init__(self, catalog_id: str, access_token: str, http: HttpClient | None = None,
                 graph_version: str = DEFAULT_GRAPH_VERSION):
        self.url = f"https://graph.facebook.com/{graph_version}/{catalog_id}/items_batch"
        self.token = access_token
        self.http = http or HttpClient()

    def _update(self, retailer_id: str, fields: dict) -> None:
        resp = self.http.request("POST", self.url, headers={"Authorization": f"Bearer {self.token}"}, json_body={
            "item_type": "PRODUCT_ITEM",
            "requests": [{"method": "UPDATE", "data": {"id": retailer_id, **fields}}],
        })
        data = resp.json() or {}
        if data.get("error"):
            raise ChannelError(f"meta items_batch error: {str(data['error'])[:300]}")
        item_errors = [e for status in data.get("validation_status") or [] for e in status.get("errors") or []]
        if item_errors:
            raise ChannelError(f"meta item {retailer_id} rejected: {str(item_errors)[:300]}")

    def discover_listing(self, sku: str) -> dict | None:
        return {"retailer_id": sku}

    def push_inventory(self, listing: dict, qty: int) -> None:
        self._update(listing["external"]["retailer_id"], {
            "inventory": int(qty),
            "availability": "in stock" if qty > 0 else "out of stock",
        })

    def push_price(self, listing: dict, price_cents: int, currency: str) -> None:
        self._update(listing["external"]["retailer_id"], {"price": f"{cents_to_decimal(price_cents)} {currency}"})

    def fetch_orders(self, since_iso: str) -> tuple[list[ChannelOrder], str]:
        return [], since_iso  # checkout happens on the Aalora/Shopify storefront
