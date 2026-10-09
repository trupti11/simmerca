"""Etsy Open API v3 adapter.

Needs an approved Etsy app with scopes listings_r listings_w transactions_r, and an OAuth refresh token.
VERIFY before go-live: the x-api-key header format (Etsy has announced key-format changes) and the
inventory payload rules for your listings' variations. Contract-test against a test listing first.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from ..errors import ChannelError
from .base import ChannelOrder, OrderLine
from .http import HttpClient

log = logging.getLogger(__name__)
API = "https://api.etsy.com/v3/application"
TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"


class EtsyTokens:
    """Holds OAuth tokens; refreshes on demand and persists rotated refresh tokens via `save`."""

    def __init__(self, keystring: str, refresh_token: str, http: HttpClient,
                 save: Callable[[dict], None] | None = None, clock: Callable[[], float] | None = None,
                 reload: Callable[[], str | None] | None = None):
        import time

        self.keystring = keystring
        self.refresh_token = refresh_token
        self.http = http
        self.save = save
        self.clock = clock or time.time
        self.reload = reload
        self._access: str | None = None
        self._expires_at = 0.0

    def access_token(self) -> str:
        if self._access and self.clock() < self._expires_at - 60:
            return self._access
        try:
            data = self._refresh()
        except ChannelError:
            # Another Lambda may have rotated the refresh token: reload it from the secret and retry once.
            latest = self.reload() if self.reload else None
            if not latest or latest == self.refresh_token:
                raise
            self.refresh_token = latest
            data = self._refresh()
        self._access = data["access_token"]
        self._expires_at = self.clock() + int(data.get("expires_in", 3600))
        if data.get("refresh_token") and data["refresh_token"] != self.refresh_token:
            self.refresh_token = data["refresh_token"]
            if self.save:
                self.save({"etsy_refresh_token": self.refresh_token})
        return self._access

    def _refresh(self) -> dict:
        resp = self.http.request("POST", TOKEN_URL, form={
            "grant_type": "refresh_token", "client_id": self.keystring, "refresh_token": self.refresh_token,
        })
        data = resp.json() or {}
        if "access_token" not in data:
            raise ChannelError("etsy token refresh failed")
        return data


class EtsyChannel:
    name = "etsy"

    def __init__(self, keystring: str, shop_id: str, tokens: EtsyTokens, http: HttpClient | None = None,
                 shared_secret: str | None = None):
        self.api_key = f"{keystring}:{shared_secret}" if shared_secret else keystring
        self.shop_id = shop_id
        self.tokens = tokens
        self.http = http or HttpClient()

    def _headers(self) -> dict:
        return {"x-api-key": self.api_key, "Authorization": f"Bearer {self.tokens.access_token()}"}

    def _get(self, path: str, params: dict | None = None):
        return self.http.request("GET", f"{API}{path}", headers=self._headers(), params=params).json() or {}

    # ----- listings -----
    def discover_listing(self, sku: str) -> dict | None:
        offset = 0
        while True:
            page = self._get(f"/shops/{self.shop_id}/listings",
                             {"state": "active", "limit": 100, "offset": offset, "includes": "Inventory"})
            for listing in page.get("results", []):
                for product in (listing.get("inventory") or {}).get("products", []):
                    if product.get("sku") == sku:
                        return {"listing_id": str(listing["listing_id"])}
            offset += 100
            if offset >= int(page.get("count", 0)) or not page.get("results"):
                return None

    def _inventory(self, listing_id: str) -> dict:
        return self._get(f"/listings/{listing_id}/inventory")

    @staticmethod
    def _to_put_body(inv: dict, sku: str, qty: int | None = None, price_cents: int | None = None) -> dict:
        """GET inventory shape -> PUT inventory shape, changing only offerings of `sku`."""
        products = []
        matched = False
        for p in inv.get("products", []):
            if p.get("is_deleted"):
                continue
            is_target = p.get("sku") == sku
            matched = matched or is_target
            offerings = []
            for o in p.get("offerings", []):
                if o.get("is_deleted"):
                    continue
                price = o.get("price") or {}
                amount = price.get("amount", 0) / (price.get("divisor") or 100)
                offering = {"price": round(amount, 2), "quantity": o.get("quantity", 0),
                            "is_enabled": o.get("is_enabled", True)}
                if o.get("readiness_state_id") is not None:  # required for physical listings
                    offering["readiness_state_id"] = o["readiness_state_id"]
                if is_target and qty is not None:
                    offering["quantity"] = qty
                    offering["is_enabled"] = qty > 0
                if is_target and price_cents is not None:
                    offering["price"] = round(price_cents / 100, 2)
                offerings.append(offering)
            products.append({
                "sku": p.get("sku", ""),
                "property_values": [
                    {k: pv.get(k) for k in ("property_id", "value_ids", "scale_id", "property_name", "values")}
                    for pv in p.get("property_values", [])
                ],
                "offerings": offerings,
            })
        if not matched:
            raise ChannelError(f"etsy listing has no product with sku {sku}")
        return {
            "products": products,
            "price_on_property": inv.get("price_on_property", []),
            "quantity_on_property": inv.get("quantity_on_property", []),
            "sku_on_property": inv.get("sku_on_property", []),
        }

    def _set_state(self, listing_id: str, state: str) -> None:
        self.http.request("PATCH", f"{API}/shops/{self.shop_id}/listings/{listing_id}", headers=self._headers(),
                          form={"state": state})

    def push_inventory(self, listing: dict, qty: int) -> None:
        listing_id = listing["external"]["listing_id"]
        inv = self._inventory(listing_id)
        body = self._to_put_body(inv, listing["sku"], qty=qty)
        all_zero = all(o["quantity"] <= 0 or not o["is_enabled"] for p in body["products"] for o in p["offerings"])
        if all_zero:
            # Etsy needs at least one enabled offering with quantity > 0; deactivate instead.
            self._set_state(listing_id, "inactive")
            return
        self.http.request("PUT", f"{API}/listings/{listing_id}/inventory", headers=self._headers(), json_body=body)
        if listing.get("last_pushed_qty") == 0:
            # we deactivated it when it sold out; bring it back now that stock exists
            self._set_state(listing_id, "active")

    def push_price(self, listing: dict, price_cents: int, currency: str) -> None:
        listing_id = listing["external"]["listing_id"]
        inv = self._inventory(listing_id)
        body = self._to_put_body(inv, listing["sku"], price_cents=price_cents)
        self.http.request("PUT", f"{API}/listings/{listing_id}/inventory", headers=self._headers(), json_body=body)

    # ----- orders -----
    def fetch_orders(self, since_iso: str) -> tuple[list[ChannelOrder], str]:
        since = int(datetime.fromisoformat(since_iso.replace("Z", "+00:00")).timestamp())
        orders: list[ChannelOrder] = []
        newest = since
        offset = 0
        while True:
            page = self._get(f"/shops/{self.shop_id}/receipts",
                             {"min_created": since, "limit": 100, "offset": offset, "was_canceled": "false"})
            for r in page.get("results", []):
                created = int(r.get("create_timestamp") or r.get("created_timestamp") or since)
                newest = max(newest, created)
                lines = [
                    OrderLine(line_id=str(t["transaction_id"]), sku=t.get("sku") or None, qty=int(t.get("quantity", 0)))
                    for t in r.get("transactions", []) if int(t.get("quantity", 0)) > 0
                ]
                orders.append(ChannelOrder("etsy", str(r["receipt_id"]), lines,
                                           datetime.fromtimestamp(created, timezone.utc).isoformat()))
            offset += 100
            if offset >= int(page.get("count", 0)) or not page.get("results"):
                break
        cursor = datetime.fromtimestamp(newest, timezone.utc).isoformat().replace("+00:00", "Z")
        return orders, cursor
