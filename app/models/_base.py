"""Common mixin for all models.

- BigInt PKs: cheap insurance against ever blowing past 2B rows.
- UTC timestamps, always. Convert at the edge.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime
from sqlalchemy.orm import Mapped, declared_attr, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    @declared_attr
    def created_at(cls) -> Mapped[datetime]:  # noqa: N805
        return mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    @declared_attr
    def updated_at(cls) -> Mapped[datetime]:  # noqa: N805
        return mapped_column(
            DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
        )


class IdMixin:
    @declared_attr
    def id(cls) -> Mapped[int]:  # noqa: N805
        return mapped_column(BigInteger, primary_key=True, autoincrement=True)
