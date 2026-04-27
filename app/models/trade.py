"""Trade — a plumber, electrician, or small trade business.

The Trade is the paying user (the 'owner' of the app). Each Trade has:
- a login (email/password)
- a public-facing profile (slug, business name, bio, credentials)
- services they offer
- availability rules and blocks
- a Stripe Connect account for receiving deposits

All customer-facing content lives under /<trade.slug>/.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import bcrypt
from flask_login import UserMixin
from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models._base import IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.availability import AvailabilityBlock, AvailabilityRule
    from app.models.booking import Booking
    from app.models.service import Service


class Trade(db.Model, IdMixin, TimestampMixin, UserMixin):
    __tablename__ = "trades"

    # Auth
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # Identity
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    business_name: Mapped[str] = mapped_column(String(120), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(120), nullable=False)
    trade_type: Mapped[str] = mapped_column(String(40), nullable=False, default="plumber")

    # Public profile
    bio: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(40))
    service_area: Mapped[str | None] = mapped_column(String(200))
    credentials: Mapped[str | None] = mapped_column(Text)  # e.g. "Gas Safe #586342"
    years_active: Mapped[int | None] = mapped_column()
    logo_url: Mapped[str | None] = mapped_column(String(500))

    # Booking defaults
    default_deposit_pence: Mapped[int] = mapped_column(default=3000)  # £30
    slot_minutes: Mapped[int] = mapped_column(default=60)
    buffer_minutes: Mapped[int] = mapped_column(default=0)
    cancellation_policy: Mapped[str] = mapped_column(String(40), default="24h_full")

    # Stripe Connect
    stripe_account_id: Mapped[str | None] = mapped_column(String(120), index=True)
    stripe_charges_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    stripe_payouts_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    # State
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)

    # Relationships
    services: Mapped[list[Service]] = relationship(
        back_populates="trade", cascade="all, delete-orphan", order_by="Service.display_order"
    )
    availability_rules: Mapped[list[AvailabilityRule]] = relationship(
        back_populates="trade", cascade="all, delete-orphan"
    )
    availability_blocks: Mapped[list[AvailabilityBlock]] = relationship(
        back_populates="trade", cascade="all, delete-orphan"
    )
    bookings: Mapped[list[Booking]] = relationship(
        back_populates="trade", cascade="all, delete-orphan"
    )

    # --- Auth helpers ---
    def set_password(self, password: str) -> None:
        self.password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    def check_password(self, password: str) -> bool:
        try:
            return bcrypt.checkpw(password.encode(), self.password_hash.encode())
        except ValueError:
            return False

    # --- Business logic ---
    @property
    def can_accept_bookings(self) -> bool:
        """True only when the trade is fully set up to take money."""
        return (
            self.is_active
            and self.is_published
            and self.stripe_charges_enabled
            and any(s.is_active for s in self.services)
        )

    @property
    def display_name(self) -> str:
        return self.business_name or self.contact_name

    def __repr__(self) -> str:
        return f"<Trade {self.slug!r}>"
