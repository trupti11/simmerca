"""Channel adapter contract. Every marketplace implements this."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class OrderLine:
    line_id: str
    sku: str | None
    qty: int


@dataclass
class ChannelOrder:
    channel: str
    order_id: str
    lines: list[OrderLine] = field(default_factory=list)
    created_at: str | None = None


class ChannelAdapter(Protocol):
    name: str

    def discover_listing(self, sku: str) -> dict | None:
        """Find this sku on the channel; return the external ids to store, or None."""
        ...

    def push_inventory(self, listing: dict, qty: int) -> None:
        """Set ABSOLUTE sellable quantity."""
        ...

    def push_price(self, listing: dict, price_cents: int, currency: str) -> None:
        ...

    def fetch_orders(self, since_iso: str) -> tuple[list[ChannelOrder], str]:
        """Orders created at/after `since_iso`, and the next cursor. Webhook-only channels return ([], since)."""
        ...


def cents_to_decimal(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}{cents // 100}.{cents % 100:02d}"
