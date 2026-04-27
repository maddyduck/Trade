"""Service — a thing a trade offers, like 'Emergency leak repair'.

Each service has its own duration, deposit, and ordering on the public page.
The deposit can be a flat amount or a percentage of an estimated total — MVP
only supports flat amounts but the schema leaves room for percentage.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models._base import IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.booking import Booking
    from app.models.trade import Trade


class Service(db.Model, IdMixin, TimestampMixin):
    __tablename__ = "services"

    trade_id: Mapped[int] = mapped_column(
        ForeignKey("trades.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    icon: Mapped[str | None] = mapped_column(String(16))  # emoji e.g. "🚿"

    duration_minutes: Mapped[int] = mapped_column(default=60, nullable=False)
    deposit_pence: Mapped[int] = mapped_column(nullable=False, default=3000)

    display_order: Mapped[int] = mapped_column(default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Surface emergency callouts visually + bypass standard slot constraints.
    is_emergency: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    trade: Mapped[Trade] = relationship(back_populates="services")
    bookings: Mapped[list[Booking]] = relationship(back_populates="service")

    @property
    def deposit_display(self) -> str:
        return f"£{self.deposit_pence / 100:.0f}" if self.deposit_pence % 100 == 0 else f"£{self.deposit_pence / 100:.2f}"

    def __repr__(self) -> str:
        return f"<Service {self.name!r} for trade={self.trade_id}>"
