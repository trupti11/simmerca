"""Domain errors. The API layer maps these to HTTP status codes."""


class SimmercaError(Exception):
    """Base error."""

    status = 400


class ValidationError(SimmercaError):
    status = 400


class NotFound(SimmercaError):
    status = 404


class Forbidden(SimmercaError):
    status = 403


class Conflict(SimmercaError):
    status = 409


class InsufficientStock(Conflict):
    def __init__(self, sku: str, on_hand: int, requested: int):
        super().__init__(f"Insufficient stock for {sku}: on hand {on_hand}, requested {requested}")
        self.sku = sku
        self.on_hand = on_hand
        self.requested = requested


class DuplicateRequest(SimmercaError):
    """Raised by the store when an idempotency key was already applied."""

    status = 200

    def __init__(self, result: dict):
        super().__init__("duplicate request")
        self.result = result


class ConcurrentModification(Conflict):
    pass


class ChannelError(SimmercaError):
    """A marketplace API call failed. Raised so SQS retries the job."""

    status = 502
