"""Dependency bundle passed to every domain function."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from .store import BlobStore, Queue, Store


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class Ctx:
    store: Store
    queue: Queue
    blobs: BlobStore
    tenant: str = "aalora"
    clock: Callable[[], datetime] = utcnow
    ids: Callable[[], str] = new_id
    intelligence_queue: Queue | None = None
    settings: dict = field(default_factory=dict)

    def now_iso(self) -> str:
        return self.clock().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
