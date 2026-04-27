"""Availability — when a trade can be booked.

Two concepts:
- AvailabilityRule: weekly recurring pattern (e.g. Mon 09:00–17:00).
  Multiple rules per day are allowed (e.g. 09:00–12:00 and 13:00–17:00).
- AvailabilityBlock: one-off exception (holiday, booked day, sick day).
  Blocks override rules — if a block covers a day, no slots are generated.

Times are stored as HH:MM strings local to the trade's timezone
(Europe/London for all V1 trades). Converted to UTC when generating slots.
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models._base import IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.trade import Trade


# 0 = Monday, 6 = Sunday. Matches Python's date.weekday().
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class AvailabilityRule(db.Model, IdMixin, TimestampMixin):
    __tablename__ = "availability_rules"

    trade_id: Mapped[int] = mapped_column(
        ForeignKey("trades.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 0..6 (Mon..Sun)
    weekday: Mapped[int] = mapped_column(nullable=False)
    # Local times, HH:MM format (e.g. "09:00", "17:30")
    start_time: Mapped[str] = mapped_column(String(5), nullable=False)
    end_time: Mapped[str] = mapped_column(String(5), nullable=False)

    trade: Mapped[Trade] = relationship(back_populates="availability_rules")

    def __repr__(self) -> str:
        return (
            f"<AvailabilityRule {WEEKDAY_NAMES[self.weekday]} "
            f"{self.start_time}-{self.end_time}>"
        )


class AvailabilityBlock(db.Model, IdMixin, TimestampMixin):
    __tablename__ = "availability_blocks"

    trade_id: Mapped[int] = mapped_column(
        ForeignKey("trades.id", ondelete="CASCADE"), nullable=False, index=True
    )
    block_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(String(200))

    trade: Mapped[Trade] = relationship(back_populates="availability_blocks")

    def __repr__(self) -> str:
        return f"<AvailabilityBlock {self.block_date} trade={self.trade_id}>"
