"""
db/models.py
────────────
SQLAlchemy ORM models for the Sparkline shared PostgreSQL schema.

Tables shared with Dhruv's enterprise adapters:
  - users          (identity, dept, designation — single source of truth)
  - audit_log      (both workstreams log here; agent_type distinguishes them)

Tables specific to Document RAG:
  - documents
  - document_versions
  - chunks
"""

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# SHARED: Users (single source of truth for identity across both workstreams)
# ─────────────────────────────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True)
    hashed_password: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Department and designation — NULL until assigned.
    # PDP/PEP use default_role as a temporary stand-in for pilot users.
    department: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    designation: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Explicit role override. Pilot users get 'pilot_user' which grants access
    # to all public documents and the pilot corpus until real attributes land.
    default_role: Mapped[str] = mapped_column(
        String(50), nullable=False, default="pilot_user"
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # file_admin = True means this user can upload + tag documents
    is_file_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    uploaded_versions: Mapped[List["DocumentVersion"]] = relationship(
        back_populates="uploader", lazy="select"
    )
    audit_entries: Mapped[List["AuditLog"]] = relationship(
        back_populates="user", lazy="select"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Document RAG: Documents
# ─────────────────────────────────────────────────────────────────────────────
class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)

    # Access control metadata — set by file-admin at ingestion time.
    # Users never self-declare these.
    allowed_departments: Mapped[Optional[List[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )  # NULL = no department restriction
    allowed_designations: Mapped[Optional[List[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )  # NULL = no designation restriction
    is_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )  # If True, PEP skips dept/desig filter

    # Pointer to whichever version is currently active in Qdrant
    current_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", use_alter=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    versions: Mapped[List["DocumentVersion"]] = relationship(
        back_populates="document",
        foreign_keys="DocumentVersion.document_id",
        lazy="select",
        order_by="DocumentVersion.version_number",
    )
    current_version: Mapped[Optional["DocumentVersion"]] = relationship(
        foreign_keys=[current_version_id], lazy="select", post_update=True
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # MinIO object key — permanent, never deleted, even when a new version is active
    minio_object_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # is_active=True means this version's chunks are in the live Qdrant index.
    # Only one version per document is active at a time.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Relationships
    document: Mapped["Document"] = relationship(
        back_populates="versions", foreign_keys=[document_id]
    )
    uploader: Mapped["User"] = relationship(back_populates="uploaded_versions")
    chunks: Mapped[List["Chunk"]] = relationship(
        back_populates="document_version", lazy="select"
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Qdrant point ID (same UUID stored in Qdrant payload for round-trip lookup)
    qdrant_point_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=False, default=uuid.uuid4
    )

    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Relationships
    document_version: Mapped["DocumentVersion"] = relationship(back_populates="chunks")


# ─────────────────────────────────────────────────────────────────────────────
# SHARED: Audit Log (both workstreams write here; agent_type distinguishes)
# ─────────────────────────────────────────────────────────────────────────────
class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True
    )

    # Which agent handled this query — matches router dispatch types.
    # 'document_rag'          answered from documents, strict grounded prompt
    # 'document_rag_blended'  documents supplied, general knowledge permitted
    # 'general'               answered from general knowledge, no retrieval used
    # 'general_fallback'      documents were searched first and did not cover it
    # 'enterprise'            enterprise agent (MCP adapter workstream)
    agent_type: Mapped[str] = mapped_column(String(50), nullable=False)

    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)

    # PDP/PEP decision fields (null for 'general' agent queries)
    pdp_decision: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True
    )  # 'allow' | 'deny' | None

    # Array of document version UUIDs that were retrieved (empty for general agent)
    retrieved_doc_version_ids: Mapped[Optional[List[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )

    response_summary: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # First 500 chars of the response
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    # Relationships
    user: Mapped[Optional["User"]] = relationship(back_populates="audit_entries")
