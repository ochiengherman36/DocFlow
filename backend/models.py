import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base

JsonType = JSON().with_variant(JSONB(), "postgresql")

BigIntId = BigInteger().with_variant(Integer(), "sqlite")


class DocumentStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class AuditAction(StrEnum):
    EXTRACTED = "extracted"
    DUPLICATE_UPLOAD = "duplicate_upload"
    EDITED = "edited"
    APPROVED = "approved"
    REJECTED = "rejected"
    SENT_TO_ZOHO = "sent_to_zoho"


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    name_key: Mapped[str] = mapped_column(String(255), unique=True)
    zoho_vendor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    zoho_vendor_organization_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class ZohoConnection(Base):
    __tablename__ = "zoho_connection"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    access_token_enc: Mapped[str] = mapped_column(Text)
    refresh_token_enc: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scope: Mapped[str | None] = mapped_column(String(500), nullable=True)
    api_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    connected_by: Mapped[str] = mapped_column(String(100))
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OAuthState(Base):
    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(String(128), primary_key=True)
    created_by: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="status_valid"),
        Index("ix_documents_invoice_key", "supplier_key", "invoice_number_key"),
        Index(
            "uq_documents_approved_invoice",
            "supplier_key",
            "invoice_number_key",
            unique=True,
            postgresql_where=text("status = 'approved'"),
            sqlite_where=text("status = 'approved'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(255))
    file_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    file_size: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default=DocumentStatus.PENDING.value, server_default="pending")
    current_data: Mapped[dict] = mapped_column(JsonType)
    supplier_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    invoice_number_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    supplier_id: Mapped[int | None] = mapped_column(
        ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True
    )
    decided_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    zoho_bill_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    zoho_bill_organization_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    zoho_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    zoho_sent_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    extraction: Mapped["Extraction | None"] = relationship(back_populates="document", uselist=False)
    audit_entries: Mapped[list["AuditEntry"]] = relationship(
        back_populates="document", order_by="AuditEntry.id"
    )


class Extraction(Base):
    __tablename__ = "extractions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), unique=True
    )
    data: Mapped[dict] = mapped_column(JsonType)
    model: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="extraction")


class AuditEntry(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_document_time", "document_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="RESTRICT"))
    extraction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("extractions.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="audit_entries")