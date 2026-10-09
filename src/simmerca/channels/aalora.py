"""Aalora storefront channel.

The Lovable storefront reads catalog + availability live from the Simmerca public API, so there is
nothing to push. Checkout runs on Shopify headless; those orders arrive via the Shopify webhook.
Linking a product to `aalora` is what makes it visible on the storefront.
"""

from __future__ import annotations

from .base import ChannelOrder


class AaloraChannel:
    name = "aalora"

    def discover_listing(self, sku: str) -> dict | None:
        return {"storefront": "aalora"}

    def push_inventory(self, listing: dict, qty: int) -> None:
        return None

    def push_price(self, listing: dict, price_cents: int, currency: str) -> None:
        return None

    def fetch_orders(self, since_iso: str) -> tuple[list[ChannelOrder], str]:
        return [], since_iso
