"""SQLAlchemy models for the AMS platform and the Easidoc digital twin."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(64), unique=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(64))
    participant_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResponsibleFunction(Base):
    __tablename__ = "responsible_functions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    __table_args__ = (UniqueConstraint("account_id", "code", name="uq_function_code"),)


class Participant(Base):
    __tablename__ = "participants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    function_id: Mapped[str] = mapped_column(ForeignKey("responsible_functions.id"))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Application(Base):
    __tablename__ = "applications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(64))
    environment: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (UniqueConstraint("account_id", "code", name="uq_application_code"),)


class ScenarioDefinition(Base):
    __tablename__ = "scenario_definitions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    application_code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text)
    variants: Mapped[list] = mapped_column(JSON, default=list)


class ScenarioExecution(Base):
    __tablename__ = "scenario_executions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenario_definitions.id"))
    variant: Mapped[str | None] = mapped_column(String(8), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="seeded")
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    incident_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    scenario_execution_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    number: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(400))
    document_type: Mapped[str] = mapped_column(String(64), default="SOP")
    status: Mapped[str] = mapped_column(String(32), default="effective")
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    effective_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    draft_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    version_label: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(400))
    body: Mapped[str] = mapped_column(Text, default="")
    document_history: Mapped[str] = mapped_column(Text, default="")
    planned_effective_date: Mapped[str] = mapped_column(String(32), default="")
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DocumentMetadata(Base):
    __tablename__ = "document_metadata"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(64))
    field_value: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowDefinition(Base):
    __tablename__ = "workflow_definitions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("application_id", "code", name="uq_workflow_def"),)


class WorkflowInstance(Base):
    __tablename__ = "workflow_instances"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    definition_id: Mapped[str] = mapped_column(ForeignKey("workflow_definitions.id"))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    scenario_execution_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    required_function_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    block_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    block_details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowTask(Base):
    __tablename__ = "workflow_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_instance_id: Mapped[str] = mapped_column(ForeignKey("workflow_instances.id"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32))
    assigned_participant_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    completed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, default=1)


class ParticipantAssignment(Base):
    __tablename__ = "participant_assignments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("workflow_tasks.id"), index=True)
    participant_id: Mapped[str] = mapped_column(ForeignKey("participants.id"))
    assigned_by: Mapped[str] = mapped_column(String(36))
    approval_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApprovalWorkflow(Base):
    """Easidoc document approval workflow. Distinct from AMS human approvals."""

    __tablename__ = "approval_workflows"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_instance_id: Mapped[str] = mapped_column(ForeignKey("workflow_instances.id"), unique=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    status: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApplicationEvent(Base):
    __tablename__ = "application_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_uuid: Mapped[str] = mapped_column(String(36), unique=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    application_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    scenario_execution_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    incident_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(80), index=True)
    source: Mapped[str] = mapped_column(String(64))
    environment: Mapped[str] = mapped_column(String(32), default="simulator")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ApplicationLog(Base):
    __tablename__ = "application_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    application_id: Mapped[str] = mapped_column(String(36), index=True)
    scenario_execution_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    incident_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    level: Mapped[str] = mapped_column(String(16))
    logger: Mapped[str] = mapped_column(String(120))
    message: Mapped[str] = mapped_column(Text)
    signature: Mapped[str] = mapped_column(String(120), default="", index=True)
    correlation_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    environment: Mapped[str] = mapped_column(String(32), default="simulator")


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    number: Mapped[str] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(32), index=True)
    environment: Mapped[str] = mapped_column(String(32), default="production")
    external_sys_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    short_description: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(40), default="new", index=True)
    priority: Mapped[str] = mapped_column(String(8), default="3")
    impact: Mapped[str] = mapped_column(String(8), default="2")
    urgency: Mapped[str] = mapped_column(String(8), default="2")
    assignment_group: Mapped[str] = mapped_column(String(200), default="")
    assigned_to: Mapped[str] = mapped_column(String(200), default="")
    category: Mapped[str] = mapped_column(String(120), default="")
    subcategory: Mapped[str] = mapped_column(String(120), default="")
    business_service: Mapped[str] = mapped_column(String(200), default="")
    cmdb_ci: Mapped[str] = mapped_column(String(200), default="")
    application_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    scenario_execution_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_notes: Mapped[str] = mapped_column(Text, default="")
    resolution_code: Mapped[str] = mapped_column(String(64), default="")
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    journal: Mapped[list] = mapped_column(JSON, default=list)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    servicenow_sync_status: Mapped[str] = mapped_column(String(64), default="not_linked")
    sys_mod_count: Mapped[str | None] = mapped_column(String(32), nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        UniqueConstraint("account_id", "number", name="uq_incident_number"),
        UniqueConstraint("account_id", "source", "external_sys_id", name="uq_incident_external"),
        Index("ix_incident_account_state", "account_id", "state"),
    )


class IncidentEvidence(Base):
    __tablename__ = "incident_evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300))
    excerpt: Mapped[str] = mapped_column(Text, default="")
    reference: Mapped[str] = mapped_column(String(200), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    account_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    orchestrator: Mapped[str] = mapped_column(String(32), default="builtin")
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentStep(Base):
    __tablename__ = "agent_steps"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    agent: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RootCauseHypothesis(Base):
    __tablename__ = "root_cause_hypotheses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    statement: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[str] = mapped_column(String(16), default="low")
    supporting: Mapped[list] = mapped_column(JSON, default=list)
    contradicting: Mapped[list] = mapped_column(JSON, default=list)
    assumptions: Mapped[list] = mapped_column(JSON, default=list)
    further_checks: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RemediationProposal(Base):
    __tablename__ = "remediation_proposals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    hypothesis_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    summary: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64))
    risk: Mapped[str] = mapped_column(String(16), default="medium")
    status: Mapped[str] = mapped_column(String(32), default="proposed")
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HumanApproval(Base):
    __tablename__ = "human_approvals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("remediation_proposals.id"), index=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64))
    progress: Mapped[dict] = mapped_column(JSON, default=dict)
    requested_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decision: Mapped[str] = mapped_column(String(40), default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ActionExecution(Base):
    __tablename__ = "action_executions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(String(36), index=True)
    approval_id: Mapped[str] = mapped_column(ForeignKey("human_approvals.id"), index=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    action: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(32))
    request: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(200), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VerificationResult(Base):
    __tablename__ = "verification_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    approval_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    recovered: Mapped[bool] = mapped_column(Boolean, default=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    checks: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditRecord(Base):
    __tablename__ = "audit_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(120))
    resource_type: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(64), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    application_code: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[str] = mapped_column(String(80), default="")
    title: Mapped[str] = mapped_column(String(400))
    doc_type: Mapped[str] = mapped_column(String(64), default="article")
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list] = mapped_column(JSON, default=list)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    application_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    incident_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(String(240), default="New conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConnectorConfig(Base):
    __tablename__ = "connector_configs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    connector: Mapped[str] = mapped_column(String(64))
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    secret_blob: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="not_configured")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (UniqueConstraint("account_id", "connector", name="uq_connector"),)


class SyncCheckpoint(Base):
    __tablename__ = "sync_checkpoints"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    connector: Mapped[str] = mapped_column(String(64))
    cursor: Mapped[str] = mapped_column(String(80), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("account_id", "connector", name="uq_checkpoint"),)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_code: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("account_id", "key", name="uq_idempotency"),)


class NumberSequence(Base):
    __tablename__ = "number_sequences"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=1000)


class PlatformSetting(Base):
    __tablename__ = "platform_settings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    key: Mapped[str] = mapped_column(String(80))
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("account_id", "key", name="uq_setting"),)
