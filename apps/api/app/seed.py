"""Reference data. Local passwords exist only when ENVIRONMENT=local."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import get_settings
from app.constants import ENVIRONMENT_LABEL
from app.models import (
    Account,
    Application,
    ConnectorConfig,
    Participant,
    PlatformSetting,
    ResponsibleFunction,
    ScenarioDefinition,
    User,
    WorkflowDefinition,
)
from app.security import hash_password
from app.services import knowledge as knowledge_service

DEV_ACCOUNTS = [
    {"email": "yugal@ams.local", "password": "admin123", "name": "Yugal", "role": "admin"},
    {"email": "admin@ams.local", "password": "LocalAdmin!2026", "name": "Avery Chen", "role": "admin"},
    {"email": "resolver@ams.local", "password": "LocalResolver!2026", "name": "Jordan Blake", "role": "resolver"},
    {"email": "investigator@ams.local", "password": "LocalInvest!2026", "name": "Mina Patel", "role": "investigator"},
    {"email": "priya.shah@ams.local", "password": "LocalReviewer!2026", "name": "Priya Shah", "role": "operator"},
    {"email": "viewer@ams.local", "password": "LocalViewer!2026", "name": "Chris Nguyen", "role": "viewer"},
]

CONNECTORS = ["servicenow", "splunk", "datadog", "opensearch", "azure_monitor", "generic_logs", "easidoc"]


def seed(db: Session) -> Account:
    settings = get_settings()
    account = db.query(Account).filter_by(code="contoso").one_or_none()
    if account is None:
        account = Account(name="Contoso Life Sciences", code="contoso")
        db.add(account)
        db.flush()
    if settings.environment == "local":
        for item in DEV_ACCOUNTS:
            _user(db, account.id, item["email"], item["name"], item["role"], item["password"])
    elif settings.bootstrap_admin_email and settings.bootstrap_admin_password:
        _user(
            db,
            account.id,
            settings.bootstrap_admin_email,
            settings.bootstrap_admin_name,
            "admin",
            settings.bootstrap_admin_password,
        )
    qa = _function(db, account.id, "QA", "Quality Assurance")
    ra = _function(db, account.id, "RA", "Regulatory Affairs")
    priya_user = db.query(User).filter_by(email="priya.shah@ams.local").one_or_none()
    priya = _participant(db, account.id, qa.id, "Priya Shah", "priya.shah@ams.local", priya_user.id if priya_user else None)
    _participant(db, account.id, qa.id, "Andre Silva", "andre.silva@ams.local", None)
    _participant(db, account.id, ra.id, "Lena Ortiz", "lena.ortiz@ams.local", None)
    if priya_user and priya_user.participant_id != priya.id:
        priya_user.participant_id = priya.id
    application = _application(db, account.id)
    _workflow(db, application.id, "auto_periodic_review", "Auto-start Periodic Review", "periodic_review")
    _workflow(db, application.id, "document_approval", "Document Approval", "approval")
    _scenario(
        db,
        "missing-periodic-review-participant",
        "Missing periodic review participant",
        "The effective document is stuck in Auto-start Periodic Review because no participant is assigned.",
        [],
    )
    _scenario(
        db,
        "missing-document-metadata",
        "Missing document metadata",
        "Approval cannot start while Document History, Planned Effective Date, or both are blank.",
        [
            {"id": "A", "label": "Document History missing"},
            {"id": "B", "label": "Planned Effective Date missing"},
            {"id": "C", "label": "Both fields missing"},
        ],
    )
    for name in CONNECTORS:
        row = db.query(ConnectorConfig).filter_by(account_id=account.id, connector=name).one_or_none()
        if row is None:
            status = "connected" if name == "easidoc" else "not_configured"
            db.add(ConnectorConfig(account_id=account.id, connector=name, settings={"environment_label": ENVIRONMENT_LABEL} if name == "easidoc" else {}, status=status))
    setting = db.query(PlatformSetting).filter_by(account_id=account.id, key="retention_days").one_or_none()
    if setting is None:
        db.add(PlatformSetting(account_id=account.id, key="retention_days", value={"days": 90}))
    knowledge_service.load_seed_files(db, account.id)
    db.flush()
    return account


def _user(db: Session, account_id: str, email: str, name: str, role: str, password: str) -> User:
    row = db.query(User).filter_by(email=email).one_or_none()
    if row:
        if email == "yugal@ams.local":
            row.name = name
            row.role = role
            row.is_active = True
            row.password_hash = hash_password(password)
        return row
    row = User(account_id=account_id, email=email, name=name, role=role, password_hash=hash_password(password))
    db.add(row)
    db.flush()
    return row


def _function(db: Session, account_id: str, code: str, name: str) -> ResponsibleFunction:
    row = db.query(ResponsibleFunction).filter_by(account_id=account_id, code=code).one_or_none()
    if row:
        return row
    row = ResponsibleFunction(account_id=account_id, code=code, name=name)
    db.add(row)
    db.flush()
    return row


def _participant(db: Session, account_id: str, function_id: str, name: str, email: str, user_id: str | None) -> Participant:
    row = db.query(Participant).filter_by(account_id=account_id, email=email).one_or_none()
    if row:
        if user_id and row.user_id != user_id:
            row.user_id = user_id
        return row
    row = Participant(account_id=account_id, function_id=function_id, name=name, email=email, user_id=user_id, active=True)
    db.add(row)
    db.flush()
    return row


def _application(db: Session, account_id: str) -> Application:
    row = db.query(Application).filter_by(account_id=account_id, code="easidoc").one_or_none()
    if row:
        return row
    row = Application(
        account_id=account_id,
        name="Easidoc",
        code="easidoc",
        environment="simulator",
        kind="digital_twin",
        label=ENVIRONMENT_LABEL,
        description="Simulated document control application used to exercise AMS investigation and remediation.",
    )
    db.add(row)
    db.flush()
    return row


def _workflow(db: Session, application_id: str, code: str, name: str, kind: str) -> None:
    row = db.query(WorkflowDefinition).filter_by(application_id=application_id, code=code).one_or_none()
    if row is None:
        db.add(WorkflowDefinition(application_id=application_id, code=code, name=name, kind=kind))


def _scenario(db: Session, scenario_id: str, name: str, description: str, variants: list) -> None:
    row = db.get(ScenarioDefinition, scenario_id)
    if row is None:
        db.add(ScenarioDefinition(id=scenario_id, application_code="easidoc", name=name, description=description, variants=variants))
    else:
        row.name = name
        row.description = description
        row.variants = variants
