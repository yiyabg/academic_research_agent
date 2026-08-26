"""Durable, replayable analysis jobs for the deployment-managed paper library.

The local corpus deliberately keeps its analysis history separate from chat
messages.  Chat is mutable UX state; these rows are the evidence/audit record
used to reproduce a report after Redis has expired.
"""

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class LocalPaperAnalysisSession(Base, TimestampMixin):
    __tablename__ = "local_paper_analysis_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    library_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_libraries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="本地文献分析")
    summary_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    audit_head_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)


class LocalPaperAnalysisJob(Base, TimestampMixin):
    __tablename__ = "local_paper_analysis_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_analysis_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    library_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_libraries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="FOCUSED")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="QUEUED", index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    request_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    retrieval_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_retrieval_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_versions_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    evidence_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    result_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    artifact_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancellation_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)


class LocalPaperAnalysisEvent(Base, TimestampMixin):
    __tablename__ = "local_paper_analysis_events"
    __table_args__ = (
        UniqueConstraint("job_id", "sequence", name="uq_local_paper_analysis_event_sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_analysis_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    payload_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    previous_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class LocalPaperAnalysisTurn(Base, TimestampMixin):
    """Full input/output audit record; never use hidden reasoning as an audit artifact."""

    __tablename__ = "local_paper_analysis_turns"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_analysis_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_analysis_jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    user_input: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_manifest_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)


class LocalPaperAnalysisLLMAttempt(Base, TimestampMixin):
    __tablename__ = "local_paper_analysis_llm_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_analysis_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class LocalPaperMemoryCandidate(Base, TimestampMixin):
    """A proposed long-term preference requiring explicit user confirmation."""

    __tablename__ = "local_paper_memory_candidates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_analysis_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_analysis_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    candidate_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING", index=True)
    confirmed_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user_research_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )


class LocalPaperLibraryProjectGrant(Base, TimestampMixin):
    """Explicit project-level permission to use a private local library."""

    __tablename__ = "local_paper_library_project_grants"
    __table_args__ = (
        UniqueConstraint("library_id", "project_id", name="uq_local_paper_library_project_grant"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    library_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_libraries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    granted_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    permission: Mapped[str] = mapped_column(String(24), nullable=False, default="ANALYZE")
