"""Single-table key layout. Every key is tenant-scoped so Simmerca can host more brands later.

pk                                sk
T#<t>#PRODUCT                     <sku>
T#<t>#BAL                         <sku>
T#<t>#LEDGER#<sku>                <iso-ts>#<event-id>
T#<t>#IDEM                        <idempotency-key>
T#<t>#SUPPLIER                    <supplier-id>
T#<t>#SUPPHONE                    <e164-phone>
T#<t>#LISTING#<sku>               <channel>
T#<t>#CHLIST#<channel>            <sku>
T#<t>#PENDING                     <e164-phone>
T#<t>#MSGSID                      <twilio-message-sid>
T#<t>#COUNTER                     <name>
T#<t>#ALERT                       <iso-ts>#<id>
T#<t>#PRICEPROP                   <id>
T#<t>#COMPARABLE                  <id>
T#<t>#BUYER                       <buyer-id>
T#<t>#QUOTE                       <quote-id>
T#<t>#CURSOR                      <channel>
T#<t>#PRODORDER                   <id>
"""

from __future__ import annotations


def _p(tenant: str, kind: str) -> str:
    return f"T#{tenant}#{kind}"


def product(t: str, sku: str) -> tuple[str, str]:
    return _p(t, "PRODUCT"), sku


def balance(t: str, sku: str) -> tuple[str, str]:
    return _p(t, "BAL"), sku


def ledger_pk(t: str, sku: str) -> str:
    return _p(t, f"LEDGER#{sku}")


def idem(t: str, key: str) -> tuple[str, str]:
    return _p(t, "IDEM"), key


def supplier(t: str, supplier_id: str) -> tuple[str, str]:
    return _p(t, "SUPPLIER"), supplier_id


def supplier_phone(t: str, phone: str) -> tuple[str, str]:
    return _p(t, "SUPPHONE"), phone


def listing(t: str, sku: str, channel: str) -> tuple[str, str]:
    return _p(t, f"LISTING#{sku}"), channel


def listings_pk(t: str, sku: str) -> str:
    return _p(t, f"LISTING#{sku}")


def channel_listing(t: str, channel: str, sku: str) -> tuple[str, str]:
    return _p(t, f"CHLIST#{channel}"), sku


def pending(t: str, phone: str) -> tuple[str, str]:
    return _p(t, "PENDING"), phone


def message_sid(t: str, sid: str) -> tuple[str, str]:
    return _p(t, "MSGSID"), sid


def counter(t: str, name: str) -> tuple[str, str]:
    return _p(t, "COUNTER"), name


def kind_pk(t: str, kind: str) -> str:
    """Partition key for a whole collection (PRODUCT, SUPPLIER, ALERT, QUOTE, ...)."""
    return _p(t, kind)


def item(t: str, kind: str, sk: str) -> tuple[str, str]:
    return _p(t, kind), sk
