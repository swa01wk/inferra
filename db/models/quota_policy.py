import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class QuotaPolicy(Base):
    __tablename__ = "quota_policies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), unique=True, nullable=False
    )
    rpm_limit: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    max_concurrent_requests: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    max_input_tokens: Mapped[int] = mapped_column(Integer, default=8192, nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, default=2048, nullable=False)
    daily_token_soft_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    daily_token_hard_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    monthly_token_hard_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
