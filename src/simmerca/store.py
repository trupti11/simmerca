"""Storage interfaces and the in-memory implementation used by tests.

Domain code depends on these interfaces only. `aws.py` holds the DynamoDB/SQS/S3 implementations.
"""

from __future__ import annotations

import copy
import threading
from typing import Any, Protocol

from .errors import ConcurrentModification, DuplicateRequest


class Store(Protocol):
    def get(self, pk: str, sk: str) -> dict | None:
        ...
    def put(self, item: dict) -> None:

        ...
    def put_if_absent(self, item: dict) -> bool:

        ...
    def delete(self, pk: str, sk: str) -> None:

        ...
    def query(self, pk: str, sk_prefix: str = "", reverse: bool = False, limit: int | None = None) -> list[dict]:

        ...
    def increment(self, pk: str, sk: str, by: int = 1) -> int:

        ...
    def apply_stock_change(
        self,
        balance_key: tuple[str, str],
        delta: int,
        idem_key: tuple[str, str],
        extra_items: list[dict],
        min_result: int = 0,
        expected_on_hand: int | None = None,
    ) -> int:
        """Atomically: fail with DuplicateRequest if idem_key exists; fail with ValueError-like
        InsufficientStock (raised by caller) when result < min_result; else update balance,
        write idem marker and extra items. With expected_on_hand, fail with
        ConcurrentModification if the stored balance differs. Returns the new on_hand."""
        ...


class Queue(Protocol):
    def send(self, message: dict) -> None:
        ...
class BlobStore(Protocol):
    def presign_put(self, key: str, content_type: str, expires: int = 900) -> str:
        ...
    def presign_get(self, key: str, expires: int = 3600) -> str:

        ...
    def get_bytes(self, key: str) -> bytes:

        ...
    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:

        ...
def stamp_balance(items: list[dict], before: int, after: int) -> list[dict]:
    """Ledger events carry the balance seen INSIDE the atomic write (accurate under concurrency)."""
    out = []
    for item in items:
        if "on_hand_after" in item:
            item = dict(item, on_hand_before=before, on_hand_after=after)
        out.append(item)
    return out


class StockFloorViolation(Exception):
    """Raised by a store when a change would take on_hand below min_result."""

    def __init__(self, on_hand: int):
        super().__init__(f"on_hand would drop below floor (current {on_hand})")
        self.on_hand = on_hand


class InMemoryStore:
    """Thread-safe dict-backed store with the same semantics as DynamoStore."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict] = {}
        self._lock = threading.RLock()

    def get(self, pk: str, sk: str) -> dict | None:
        with self._lock:
            item = self._items.get((pk, sk))
            return copy.deepcopy(item) if item else None

    def put(self, item: dict) -> None:
        with self._lock:
            self._items[(item["pk"], item["sk"])] = copy.deepcopy(item)

    def put_if_absent(self, item: dict) -> bool:
        with self._lock:
            key = (item["pk"], item["sk"])
            if key in self._items:
                return False
            self._items[key] = copy.deepcopy(item)
            return True

    def delete(self, pk: str, sk: str) -> None:
        with self._lock:
            self._items.pop((pk, sk), None)

    def query(self, pk: str, sk_prefix: str = "", reverse: bool = False, limit: int | None = None) -> list[dict]:
        with self._lock:
            rows = [copy.deepcopy(v) for (p, s), v in self._items.items() if p == pk and s.startswith(sk_prefix)]
        rows.sort(key=lambda r: r["sk"], reverse=reverse)
        return rows[:limit] if limit else rows

    def increment(self, pk: str, sk: str, by: int = 1) -> int:
        with self._lock:
            item = self._items.setdefault((pk, sk), {"pk": pk, "sk": sk, "value": 0})
            item["value"] = int(item.get("value", 0)) + by
            return item["value"]

    def apply_stock_change(
        self,
        balance_key: tuple[str, str],
        delta: int,
        idem_key: tuple[str, str],
        extra_items: list[dict],
        min_result: int = 0,
        expected_on_hand: int | None = None,
    ) -> int:
        with self._lock:
            existing = self._items.get(idem_key)
            if existing:
                raise DuplicateRequest(copy.deepcopy(existing.get("result", {})))
            bal = self._items.get(balance_key) or {"pk": balance_key[0], "sk": balance_key[1], "on_hand": 0, "version": 0}
            if expected_on_hand is not None and int(bal["on_hand"]) != expected_on_hand:
                raise ConcurrentModification("balance changed since it was read")
            new = int(bal["on_hand"]) + delta
            if new < min_result:
                raise StockFloorViolation(int(bal["on_hand"]))
            bal = dict(bal, on_hand=new, version=int(bal.get("version", 0)) + 1)
            self._items[balance_key] = bal
            self._items[idem_key] = {"pk": idem_key[0], "sk": idem_key[1], "result": {"on_hand": new}}
            for item in stamp_balance(extra_items, new - delta, new):
                self._items[(item["pk"], item["sk"])] = copy.deepcopy(item)
            return new

    # test helper
    def all_items(self) -> list[dict[str, Any]]:
        with self._lock:
            return [copy.deepcopy(v) for v in self._items.values()]


class InMemoryQueue:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def send(self, message: dict) -> None:
        self.messages.append(copy.deepcopy(message))

    def drain(self) -> list[dict]:
        out, self.messages = self.messages, []
        return out


class InMemoryBlobStore:
    def __init__(self) -> None:
        self.blobs: dict[str, tuple[bytes, str]] = {}

    def presign_put(self, key: str, content_type: str, expires: int = 900) -> str:
        return f"https://blob.test/put/{key}?ct={content_type}"

    def presign_get(self, key: str, expires: int = 3600) -> str:
        return f"https://blob.test/get/{key}"

    def get_bytes(self, key: str) -> bytes:
        return self.blobs[key][0]

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self.blobs[key] = (data, content_type)
