"""Request bodies."""

from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    email: str
    password: str


class OidcCallbackIn(BaseModel):
    code: str
    state: str


class IncidentIn(BaseModel):
    short_description: str = Field(min_length=4, max_length=500)
    description: str = ""
    priority: str = "3"
    application_id: str | None = None
    document_id: str | None = None


class LinkIn(BaseModel):
    number: str


class DecideIn(BaseModel):
    decision: str
    comment: str = ""
    modifications: dict | None = None


class SeedIn(BaseModel):
    variant: str | None = None


class VersionIn(BaseModel):
    expected_version: int


class AssignIn(BaseModel):
    participant_id: str
    expected_version: int
    approval_id: str


class MetadataIn(BaseModel):
    expected_version: int
    document_history: str | None = None
    planned_effective_date: str | None = None
    approval_id: str | None = None
    human_confirmation: bool = False


class ConversationIn(BaseModel):
    application_id: str | None = None
    document_id: str | None = None


class MessageIn(BaseModel):
    content: str


class ConnectorIn(BaseModel):
    instance_url: str = ""
    username: str = ""
    password: str | None = None
    assignment_group: str = ""
    priorities: list[str] = []
    historical_days: int = 30
    sync_frequency_seconds: int = 300
    write_mode: str = "read_only"
    auto_sync: bool = False


class KnowledgeTransitionIn(BaseModel):
    status: str


class UserIn(BaseModel):
    email: str
    name: str
    password: str
    role: str


class RetentionIn(BaseModel):
    days: int = Field(ge=7, le=3650)


class SearchIn(BaseModel):
    query: str
    application_code: str | None = None
