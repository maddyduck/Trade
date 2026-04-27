"""Booking photos — uploaded by customer at booking time, viewed by trade.

Storage backend (local disk in dev, S3/R2 in prod) is configured at app
level. The model only stores the storage key and a public URL.
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models._base import IdMixin, TimestampMixin


class BookingPhoto(IdMixin, TimestampMixin, db.Model):
    __tablename__ = "booking_photos"

    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    # Direct fetch URL — for local backend, this is /uploads/<key>; for s3, the public URL.
    public_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(String(1000))
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    booking = relationship("Booking", backref="photos")

    def __repr__(self) -> str:
        return f"<BookingPhoto {self.id} for booking {self.booking_id}>"
