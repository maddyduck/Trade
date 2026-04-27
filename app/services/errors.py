"""Domain exceptions. Views catch these and translate to HTTP responses."""
from __future__ import annotations


class DomainError(Exception):
    """Base class. Always user-safe to display."""

    def __init__(self, message: str, *, code: str = "error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class SlotUnavailable(DomainError):
    """The requested slot is already booked or outside availability."""


class BookingNotFound(DomainError):
    pass


class InvalidTransition(DomainError):
    """Tried to move a booking to a status it can't reach from its current one."""


class TradeNotBookable(DomainError):
    """Trade has no Stripe, no services, or is unpublished."""


class PolicyViolation(DomainError):
    """Action blocked by a policy — e.g. cancelling after cut-off for a refund."""
