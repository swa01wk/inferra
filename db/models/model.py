import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class Model(Base):
    __tablename__ = "models"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    hf_repo: Mapped[str] = mapped_column(String, nullable=False)
    architecture: Mapped[str | None] = mapped_column(String, nullable=True)
    parameter_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dtype: Mapped[str] = mapped_column(String, default="bfloat16", nullable=False)
    context_length: Mapped[int] = mapped_column(Integer, default=8192, nullable=False)
    status: Mapped[str] = mapped_column(String, default="available", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
