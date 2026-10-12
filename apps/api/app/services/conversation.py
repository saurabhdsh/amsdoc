"""Conversation assistant grounded in application evidence."""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import Application, Conversation, ConversationMessage, Document, Incident, User, utcnow
from app.security import has_permission
from app.services import easidoc, investigation
from app.services.diagnosis import analyze
from app.util import iso


def create_conversation(db: Session, user: User, application_id: str | None, document_id: str | None) -> dict:
    if document_id:
        document = _document(db, user, document_id)
        application_id = document.application_id
    elif application_id:
        _application(db, user, application_id)
    row = Conversation(account_id=user.account_id, user_id=user.id, application_id=application_id, document_id=document_id, title="New conversation")
    db.add(row)
    db.flush()
    return _conversation_dict(db, row)


def update_conversation(db: Session, user: User, conversation_id: str, application_id: str | None, document_id: str | None) -> dict:
    row = _conversation(db, user, conversation_id)
    if document_id:
        document = _document(db, user, document_id)
        row.document_id = document.id
        row.application_id = document.application_id
        incident = _open_incident(db, document.id)
        row.incident_id = None if incident is None else incident.id
    elif application_id:
        _application(db, user, application_id)
        row.application_id = application_id
        row.document_id = None
    db.flush()
    return _conversation_dict(db, row)


def list_conversations(db: Session, user: User) -> list[dict]:
    rows = (
        db.query(Conversation)
        .filter(Conversation.account_id == user.account_id, Conversation.user_id == user.id)
        .order_by(Conversation.created_at.desc())
        .all()
    )
    return [_conversation_dict(db, row) for row in rows]


def get_conversation(db: Session, user: User, conversation_id: str) -> dict:
    row = _conversation(db, user, conversation_id)
    payload = _conversation_dict(db, row)
    payload["messages"] = [_message_dict(message) for message in _messages(db, row.id)]
    payload["context"] = _context(db, user, row, run_investigation=False)
    return payload


def post_message(db: Session, user: User, conversation_id: str, content: str) -> dict:
    if not content.strip():
        raise AppError("EMPTY_MESSAGE", "Enter a message.", 422)
    if len(content) > 8000:
        raise AppError("MESSAGE_TOO_LONG", "Message exceeds 8000 characters.", 422)
    row = _conversation(db, user, conversation_id)
    if row.title == "New conversation":
        row.title = content.strip()[:80]
    db.add(ConversationMessage(conversation_id=row.id, role="user", content=content.strip()))
    if _confirming_local_incident(db, row, content):
        answer, citations, context = _open_local_incident(db, user, row)
        message = ConversationMessage(conversation_id=row.id, role="assistant", content=answer, citations=citations, context=_public_context(context))
        db.add(message)
        db.flush()
        return {"conversation": _conversation_dict(db, row), "message": _message_dict(message), "context": _public_context(context)}
    if not _about_easidoc(content):
        context = _prior_solutions(db, user, content)
        answer = context["answer_card"]["plain"]
        citations = context["answer_card"]["citations"]
        message = ConversationMessage(conversation_id=row.id, role="assistant", content=answer, citations=citations, context=_public_context(context))
        db.add(message)
        db.flush()
        return {"conversation": _conversation_dict(db, row), "message": _message_dict(message), "context": _public_context(context)}
    if not row.document_id:
        chosen = _document_named_in(db, user, content)
        if chosen is not None:
            row.document_id = chosen.id
            row.application_id = chosen.application_id
            db.flush()
    context = _context(db, user, row, run_investigation=True)
    context["remediation"] = _apply_confirmed_metadata(db, user, content, context) or _apply_confirmed_participant(db, user, content, context)
    answer, citations = _answer(content, context)
    if context.get("incident"):
        row.incident_id = context["incident"]["id"]
    if (context.get("remediation") or {}).get("status") in {"resolved", "waiting"}:
        context = _context(db, user, row, run_investigation=False)
    message = ConversationMessage(
        conversation_id=row.id,
        role="assistant",
        content=answer,
        citations=citations,
        context=_public_context(context),
    )
    db.add(message)
    db.flush()
    return {"conversation": _conversation_dict(db, row), "message": _message_dict(message), "context": _public_context(context)}


def _context(db: Session, user: User, row: Conversation, *, run_investigation: bool) -> dict:
    application = db.get(Application, row.application_id) if row.application_id else _default_application(db, user)
    document = _document(db, user, row.document_id) if row.document_id else None
    if document is None:
        documents = easidoc.list_documents(db, user)
        return {"application": _application_dict(application), "documents": documents, "document": None, "response_source": "evidence_engine"}
    snapshot = easidoc.snapshot(db, document)
    from app.connectors.observability import build_connector

    logs = build_connector("easidoc", db, user.account_id).query_logs({"document_id": document.id, "limit": 30})
    signatures = logs.get("signatures") or []
    analysis = analyze(snapshot, signatures)
    from app.services import knowledge as knowledge_service

    query = " ".join([snapshot["number"], snapshot["title"], analysis["case"], *signatures])
    hits = knowledge_service.search(db, user, query, application_code="easidoc", limit=3)
    incident = _open_incident(db, document.id)
    approval = None
    if incident and run_investigation and has_permission(user, "incident.investigate"):
        result = investigation.investigate(db, user, incident.id)
        approval_id = result.get("approval_id")
        if approval_id:
            from app.models import HumanApproval

            approval = db.get(HumanApproval, approval_id)
        incident = db.get(Incident, incident.id)
    elif incident:
        from app.models import HumanApproval

        approval = (
            db.query(HumanApproval)
            .filter(HumanApproval.incident_id == incident.id)
            .order_by(HumanApproval.created_at.desc())
            .first()
        )
    return {
        "application": _application_dict(application),
        "document": snapshot,
        "incident": None if incident is None else _incident_brief(incident),
        "logs": [row for row in logs.get("logs") or [] if row.get("level") in {"ERROR", "WARN"}][:5],
        "knowledge": hits,
        "diagnosis": analysis,
        "approval": None
        if approval is None
        else {"id": approval.id, "status": approval.status, "summary": (approval.payload or {}).get("summary", "")},
        "response_source": "evidence_engine",
    }


def _document_named_in(db: Session, user: User, content: str) -> Document | None:
    documents = easidoc.list_documents(db, user)
    text = content.lower()
    by_number = [item for item in documents if item["number"].lower() in text]
    if len(by_number) == 1:
        return _document(db, user, by_number[0]["id"])
    if len(by_number) > 1:
        return None
    by_title = [item for item in documents if len(item["title"]) > 8 and item["title"].lower() in text]
    if len(by_title) == 1:
        return _document(db, user, by_title[0]["id"])
    return None


def _apply_confirmed_metadata(db: Session, user: User, content: str, context: dict) -> dict | None:
    diagnosis = context.get("diagnosis") or {}
    if diagnosis.get("case") != "missing_metadata":
        return None
    provided = _parse_metadata(content)
    if not provided:
        return None
    approval = context.get("approval") or {}
    document = context.get("document") or {}
    incident = context.get("incident") or {}
    if not approval.get("id"):
        return {"status": "blocked", "message": "There is no pending approval that can receive these values."}
    missing = set(document.get("missing_fields") or [])
    labels = {"document_history": "Document history", "planned_effective_date": "Planned effective date"}
    still_needed = [labels[field] for field in ("document_history", "planned_effective_date") if field in missing and field not in provided]
    if still_needed:
        kept = []
        if "document_history" in provided:
            kept.append(f"Document history: {provided['document_history']}")
        if "planned_effective_date" in provided:
            kept.append(f"Planned effective date: {provided['planned_effective_date']}")
        message = "I still need " + " and ".join(still_needed) + ". I will not invent that value."
        if kept:
            message += " I kept " + "; ".join(kept) + "."
        return {"status": "incomplete", "message": message}
    error = easidoc.validate_metadata_values(provided)
    if error:
        return {"status": "invalid", "message": f"{error} Send both confirmed values again."}
    from app.services import executor

    try:
        executor.decide(db, user, approval["id"], "approve", "Values confirmed in conversation.", provided)
        result = executor.execute(db, user, approval["id"])
    except AppError as exc:
        return {"status": "failed", "message": exc.message}
    number = document.get("number") or "The document"
    if result.get("status") == "completed" and result.get("incident_state") == "resolved":
        from app.models import Document

        saved = easidoc.snapshot(db, db.get(Document, document.get("document_id"))).get("draft") or {}
        confirmed = []
        if "document_history" in provided:
            confirmed.append(f"Document history: {saved.get('document_history')}")
        if "planned_effective_date" in provided:
            confirmed.append(f"Planned effective date: {saved.get('planned_effective_date')}")
        return {
            "status": "resolved",
            "message": (
                f"Saved the values you confirmed on {number}. "
                + ". ".join(confirmed)
                + f". Validation passed, the approval workflow started, and incident {incident.get('number', '')} is resolved."
            ),
        }
    return {
        "status": result.get("status") or "failed",
        "message": (
            f"The values were saved on the approval, and the remediation finished as {result.get('status')}. "
            f"Incident state: {result.get('incident_state')}."
        ),
    }


def _parse_metadata(content: str) -> dict:
    values: dict[str, str] = {}
    date = re.search(
        r"(?:planned\s+effective\s+date|effective\s+date)\s*(?:is|:)\s*(\d{4}-\d{2}-\d{2})",
        content,
        re.IGNORECASE,
    )
    if date:
        values["planned_effective_date"] = date.group(1)
    history = re.search(
        r"(?:document\s+history|history)\s*(?:is|:)\s*(.+?)(?=(?:planned\s+effective\s+date|effective\s+date)\b|$)",
        content,
        re.IGNORECASE | re.DOTALL,
    )
    if history:
        text = re.sub(r"\s+", " ", history.group(1)).strip(" \t.;")
        if text:
            values["document_history"] = text
    return values


def _apply_confirmed_participant(db: Session, user: User, content: str, context: dict) -> dict | None:
    diagnosis = context.get("diagnosis") or {}
    document = context.get("document") or {}
    people = document.get("eligible_participants") or []
    named = _participants_named_in(content, people)
    if diagnosis.get("case") == "review_incomplete":
        if not named:
            return None
        assigned = _assigned_name(document)
        if len(named) > 1:
            return {"status": "ambiguous", "message": "Name one participant. " + " ".join(_participant_lines(people))}
        person = named[0]
        if assigned and assigned != person["name"]:
            return {
                "status": "waiting",
                "message": f"{assigned} is already assigned on {document.get('number')}. {person['name']} is not the reviewer for this task.",
            }
        return {
            "status": "waiting",
            "message": (
                f"{person['name']} is already assigned to the Auto Periodic Review on {document.get('number')}. "
                f"{person['name']} must sign in and complete that review. Approval starts after PERIODIC_REVIEW_COMPLETED."
            ),
        }
    if diagnosis.get("case") != "missing_participant" or not named:
        return None
    if len(named) > 1:
        return {"status": "ambiguous", "message": "Name one Quality Assurance participant. " + " ".join(_participant_lines(people))}
    person = named[0]
    approval = context.get("approval") or {}
    incident = context.get("incident") or {}
    if not approval.get("id"):
        return {"status": "blocked", "message": "There is no pending approval that can receive this participant."}
    from app.services import executor

    try:
        executor.decide(db, user, approval["id"], "approve", f"{person['name']} confirmed in conversation.", {"participant_id": person["id"]})
        result = executor.execute(db, user, approval["id"])
    except AppError as exc:
        return {"status": "failed", "message": exc.message}
    number = document.get("number") or "The document"
    if result.get("status") == "waiting":
        finish = (
            f"{person['name']} must sign in and complete the Auto Periodic Review."
            if person.get("has_user")
            else f"{person['name']} has no login, so this assignment cannot be completed."
        )
        return {
            "status": "waiting",
            "message": (
                f"Assigned {person['name']} to the Auto Periodic Review on {number}. {finish} "
                f"Approval starts after PERIODIC_REVIEW_COMPLETED. Incident {incident.get('number', '')} stays open."
            ),
        }
    if result.get("status") == "completed" and result.get("incident_state") == "resolved":
        return {
            "status": "resolved",
            "message": f"{person['name']} completed the path on {number}. Incident {incident.get('number', '')} is resolved.",
        }
    return {
        "status": result.get("status") or "failed",
        "message": f"The participant was accepted, and the remediation finished as {result.get('status')}. Incident state: {result.get('incident_state')}.",
    }


def _participants_named_in(content: str, people: list[dict]) -> list[dict]:
    text = content.lower()
    words = set(re.findall(r"[a-z]+", text))
    matched = [person for person in people if person.get("name", "").lower() in text]
    if matched:
        return matched
    partial = []
    for person in people:
        tokens = [token for token in re.findall(r"[a-z]+", person.get("name", "").lower()) if len(token) > 2]
        if not tokens:
            continue
        if all(token in words for token in tokens) or (tokens[0] in words and sum(tokens[0] in other.get("name", "").lower() for other in people) == 1):
            partial.append(person)
    return partial


def _participant_lines(people: list[dict]) -> list[str]:
    lines = []
    for person in people:
        if person.get("has_user"):
            lines.append(f"- {person['name']} can be assigned and can complete the review.")
        else:
            lines.append(f"- {person['name']} can be assigned, but has no login, so that person cannot complete the review.")
    return lines or ["- No eligible Quality Assurance participant is available."]


def _assigned_name(document: dict) -> str:
    review = document.get("review") or {}
    for task in review.get("tasks") or []:
        if task.get("assigned_participant_name"):
            return task["assigned_participant_name"]
    return ""


def _answer(content: str, context: dict) -> tuple[str, list]:
    if context.get("document") is None:
        documents = context.get("documents") or []
        if not documents:
            text = (
                "No Easidoc simulator document is loaded. Seed a scenario from the Easidoc workspace, then select that document. "
                "I will not guess which failure you mean."
            )
        else:
            lines = [f"- {item['number']}: {item['title']} ({item['status']})" for item in documents]
            text = "Which document should I investigate?\n" + "\n".join(lines)
        return text, []
    diagnosis = context["diagnosis"]
    document = context["document"]
    number = document["number"]
    citations = [
        {"id": hit["id"], "title": hit["title"], "source": hit["source_label"], "excerpt": hit["excerpt"][:240]}
        for hit in context.get("knowledge") or []
    ]
    missing = document.get("missing_fields") or []
    if diagnosis["case"] == "missing_participant":
        choices = _participant_lines(document.get("eligible_participants") or [])
        body = (
            f"{number} cannot start approval because the Auto Periodic Review task has no participant. "
            "The simulator state and the REVIEW_PARTICIPANT_NOT_FOUND evidence agree. "
            "Document History and Planned Effective Date are already populated. "
            "Reply with one Quality Assurance participant to assign.\n"
            + "\n".join(choices)
            + "\nAssignment does not complete the review. Approval can start only after that person completes it."
        )
    elif diagnosis["case"] == "missing_metadata":
        labels = ", ".join(field.replace("_", " ") for field in missing) or "mandatory metadata"
        examples = []
        if "document_history" in missing:
            examples.append("Document history: Revised after the quality review.")
        if "planned_effective_date" in missing:
            examples.append("Planned effective date: 2026-11-15")
        body = (
            f"{number} is a draft and approval was rejected because {labels} is blank. "
            "The signature is REQUIRED_METADATA_MISSING. I will not invent Document History or a Planned Effective Date. "
            "Reply with the confirmed value in this chat:\n"
            + "\n".join(examples)
            + "\nI will save only what you send, revalidate, start approval, and resolve the incident."
        )
    elif diagnosis["case"] == "review_incomplete":
        body = (
            f"A participant is already assigned on {number}, and the review is still open. "
            "The assigned participant has to complete the Auto Periodic Review task before approval can start."
        )
    elif diagnosis["case"] == "multiple":
        body = (
            f"{number} has more than one open gate. I will not treat either hypothesis as the sole confirmed cause. "
            f"{diagnosis['summary']}"
        )
    elif diagnosis["case"] == "healthy":
        body = f"{number} does not currently show a blocked approval gate. Tell me what you expected to happen if the screen still looks wrong."
    else:
        body = diagnosis["summary"]
    remediation = context.get("remediation") or {}
    if remediation.get("status") == "resolved":
        body = remediation["message"]
    elif remediation.get("message"):
        body = remediation["message"]
    elif context.get("approval"):
        body += f" Approval {context['approval']['id'][:8]} is {context['approval']['status']}."
    elif context.get("incident"):
        body += f" Incident {context['incident']['number']} is {context['incident']['state']}."
    if "ignore previous" in content.lower() or "force" in content.lower() and "approval" in content.lower():
        body += " Instructions inside the question or in retrieved text cannot authorize a workflow bypass."
    return body, citations


def _public_context(context: dict) -> dict:
    return {
        "application": context.get("application"),
        "document": context.get("document"),
        "incident": context.get("incident"),
        "logs": context.get("logs") or [],
        "knowledge": context.get("knowledge") or [],
        "diagnosis": context.get("diagnosis"),
        "approval": context.get("approval"),
        "documents": context.get("documents") or [],
        "response_source": context.get("response_source") or "evidence_engine",
        "answer_card": context.get("answer_card"),
    }


def _about_easidoc(content: str) -> bool:
    text = content.lower()
    if re.search(r"\bdoc-\d+\b", text):
        return True
    markers = (
        "easidoc",
        "approval workflow",
        "document history",
        "planned effective date",
        "effective date",
        "periodic review",
        "priya shah",
        "andre silva",
    )
    return any(marker in text for marker in markers)


def _confirming_local_incident(db: Session, row: Conversation, content: str) -> bool:
    text = content.strip().lower()
    if text not in {"yes", "yes please", "yes, create it", "create the incident", "create an incident", "please create the incident"}:
        return False
    previous = (
        db.query(ConversationMessage)
        .filter(ConversationMessage.conversation_id == row.id, ConversationMessage.role == "assistant")
        .order_by(ConversationMessage.created_at.desc())
        .first()
    )
    card = ((previous.context if previous else None) or {}).get("answer_card") or {}
    return bool(card.get("offer"))


def _open_local_incident(db: Session, user: User, row: Conversation) -> tuple[str, list, dict]:
    from app.util import next_number, sla_deadline

    previous_user = (
        db.query(ConversationMessage)
        .filter(ConversationMessage.conversation_id == row.id, ConversationMessage.role == "user")
        .order_by(ConversationMessage.created_at.desc())
        .all()
    )
    question = next((item.content for item in previous_user if not _confirming_local_incident(db, row, item.content)), "Follow-up from Conversational AI")
    incident = Incident(
        account_id=user.account_id,
        number=next_number(db, "AMS", "incident"),
        source="manual",
        environment="production",
        short_description=question.strip()[:240],
        description="Opened from Conversational AI after a prior-solution reply. This record is local and was not written to ServiceNow.",
        priority="3",
        state="new",
        sla_due_at=sla_deadline("3"),
        servicenow_sync_status="not_linked",
        raw_payload={"origin": "conversation"},
    )
    db.add(incident)
    db.flush()
    row.incident_id = incident.id
    plain = f"Created local incident {incident.number}. It is new, and it was not written to ServiceNow."
    card = {"tool": "check_prior_solutions", "lead": plain, "sections": [], "codes": [], "references": [], "logs": "", "offer": "", "plain": plain, "citations": []}
    context = {"answer_card": card, "incident": {"id": incident.id, "number": incident.number, "state": incident.state}, "response_source": "prior_solutions"}
    return plain, [], context


def _prior_solutions(db: Session, user: User, content: str) -> dict:
    import html

    from app.services import knowledge as knowledge_service
    from app.services.servicenow import public_config
    from app.util import tokenize

    ignored = {
        "create", "created", "issue", "issues", "unable", "error", "failed", "failure", "prod", "user", "users",
        "new", "application", "system", "team", "resolve", "resolved", "solution", "solutions", "mes", "pharmasuite",
        "help", "please", "able", "need", "needs", "found", "executed", "execute",
    }
    synonyms = {"find": "missing", "finding": "missing"}
    products = {"pharmasuite", "mes", "outlook", "easidoc", "concur", "crm", "veeva"}
    raw = set(tokenize(content))
    question_products = raw & products
    focus = {synonyms.get(token, token) for token in raw} - ignored - products
    incidents = (
        db.query(Incident)
        .filter(Incident.account_id == user.account_id, Incident.source == "servicenow", Incident.state.in_(["resolved", "closed"]), Incident.close_notes != "")
        .all()
    )
    ranked = []
    for incident in incidents:
        short = set(tokenize(incident.short_description)) - ignored - products
        notes = set(tokenize(incident.close_notes)) - ignored - products
        incident_products = set(tokenize(incident.short_description)) & products
        if question_products and not (question_products & incident_products):
            continue
        shared = focus & short
        expanded = _same_problem(short)
        related = focus & expanded if expanded != short else set()
        if len(shared) < 2 and not (question_products and related):
            continue
        overlap = (shared or related) | (focus & notes)
        score = len(overlap) / max(len(focus), 1)
        weight = sum({"authentication": 4, "login": 4, "logon": 4, "session": 3, "password": 2, "locked": 2, "unlock": 2, "access": 1}.get(token, 0) for token in short)
        ranked.append((weight, len(shared), score, 0 if "-" in incident.number else 1, incident))
    ranked.sort(key=lambda item: (item[0], item[1], item[2], item[3]), reverse=True)
    chosen = []
    seen = set()
    for _weight, _shared, score, _clean, incident in ranked:
        key = incident.external_number or incident.number
        if key in seen:
            continue
        seen.add(key)
        chosen.append((score, incident))
        if len(chosen) == 2:
            break
    from app.models import KnowledgeDocument

    articles = knowledge_service.search(db, user, content, limit=5)
    kept = []
    for item in articles:
        article_tokens = set(tokenize(f"{item['title']} {item['excerpt']}")) - ignored
        document = db.get(KnowledgeDocument, item["id"])
        blob = f"{item['title']} {(document.content if document else item['excerpt'])}".lower()
        if item["score"] < 0.08 or not (focus & article_tokens):
            continue
        if "material" in focus and ("material" not in blob or not any(word in blob for word in ("sap", "pharmasuite", "idoc"))):
            continue
        if question_products and not any(word in blob for word in question_products):
            continue
        kept.append(item)
    articles = kept[:2]
    instance = ""
    try:
        instance = (public_config(db, user).get("instance_url") or "").rstrip("/")
    except Exception:
        instance = ""
    references = []
    citations = []
    for score, incident in chosen:
        number = incident.external_number or incident.number
        url = f"{instance}/incident.do?sys_id={incident.external_sys_id}" if instance.startswith("https://") and incident.external_sys_id else ""
        references.append(
            {
                "kind": "INC",
                "number": number,
                "title": incident.short_description,
                "similarity": round(score * 100, 1),
                "url": url,
            }
        )
        citations.append({"id": incident.id, "title": f"{number} — {incident.short_description}", "source": "ServiceNow incident", "excerpt": " ".join((incident.close_notes or "").split())[:240]})
    for article in articles:
        number = article["title"].split("—")[0].strip()
        url = ""
        if instance.startswith("https://") and article.get("id"):
            from app.models import KnowledgeDocument

            document = db.get(KnowledgeDocument, article["id"])
            if document and document.source_id:
                url = f"{instance}/kb_knowledge.do?sys_id={document.source_id}"
        references.append(
            {
                "kind": "KB",
                "number": number,
                "title": article["title"],
                "similarity": round(float(article["score"]) * 100, 1),
                "url": url,
            }
        )
        citations.append({"id": article["id"], "title": article["title"], "source": article["source_label"], "excerpt": article["excerpt"][:240]})
    best_notes = " ".join((chosen[0][1].close_notes or "").split()) if chosen else ""
    best_number = (chosen[0][1].external_number or chosen[0][1].number) if chosen else ""
    codes = []
    from app.models import KnowledgeDocument

    for article in articles:
        full = db.get(KnowledgeDocument, article["id"])
        found = _status_codes(html.unescape(full.content if full else ""))
        if len(found) > len(codes):
            codes = found
    identifier = re.search(r"\b[A-Za-z]{0,8}\d{4,}\b", content)
    if identifier:
        log_line = f"The question names {identifier.group(0)}. No stored syslog line was attached to this reply."
    else:
        log_line = "No batch or material number was in the question, so ServiceNow syslog was not searched."
    if chosen:
        lead = f"I found a closed incident for this symptom. {best_number} records the resolution below."
        shown = best_notes[:700]
        sections = [
            {"label": "What the closed incident says", "body": shown},
            {
                "label": "What to do",
                "body": f"Use the resolution recorded on {best_number}. The status codes below come from the published article, and the incident note is the evidence for which status blocked MES.",
            },
        ]
    else:
        lead = "I did not find a closed incident whose description matches this question."
        sections = [{"label": "What I can confirm", "body": "Published knowledge is listed below. I will not invent a cause or a material status."}]
    offer = ""
    if chosen and "sap" in best_notes.lower():
        offer = "Would you like me to create an incident for the SAP team to check and correct the material status, or does this resolve your issue?"
    elif chosen:
        offer = "Would you like me to create a local incident for this issue, or does this resolve it?"
    plain_parts = ["Using tool: check_prior_solutions", "", lead, ""]
    for section in sections:
        plain_parts.append(section["label"])
        plain_parts.append(section["body"])
        plain_parts.append("")
    if codes:
        plain_parts.append("Status codes from the article")
        for code in codes:
            plain_parts.append(f"- {code['code']} — {code['detail']}")
        plain_parts.append("")
    plain_parts.append(log_line)
    if offer:
        plain_parts.extend(["", offer])
    if references:
        plain_parts.extend(["", "References"])
        for item in references:
            line = f"- [{item['kind']}] {item['number']} (similarity {item['similarity']}%): {item['title']}"
            if item["url"]:
                line += f" — {item['url']}"
            plain_parts.append(line)
    card = {
        "tool": "check_prior_solutions",
        "lead": lead,
        "sections": sections,
        "codes": codes,
        "references": references,
        "logs": log_line,
        "offer": offer,
        "plain": "\n".join(plain_parts).strip(),
        "citations": citations,
    }
    knowledge = [{"title": item["title"], "excerpt": item["excerpt"][:280]} for item in articles]
    logs = [{"signature": "close_note" if chosen else "syslog", "message": best_notes[:360] if chosen else log_line}]
    if chosen:
        logs.append({"signature": "syslog", "message": log_line})
    return {"answer_card": card, "knowledge": knowledge, "logs": logs, "response_source": "prior_solutions"}


def _same_problem(tokens: set[str]) -> set[str]:
    families = (
        {"login", "logon", "authentication", "session", "password", "locked", "unlock", "access"},
    )
    expanded = set(tokens)
    for family in families:
        if tokens & family:
            expanded |= family
    return expanded


def _status_codes(text: str) -> list[dict]:
    import html

    clean = html.unescape(text or "")
    found = []
    patterns = (
        ("SP", r"SP\s*\(([^)]+)\)"),
        ("S3", r"S3\s*[^A-Za-z0-9\n]{1,4}\s*([A-Za-z][^.\n]{3,80})"),
        ("S2", r"S2\s*[^A-Za-z0-9\n]{1,4}\s*([A-Za-z][^.\n]{3,80})"),
    )
    for code, pattern in patterns:
        match = re.search(pattern, clean)
        if match:
            found.append({"code": code, "detail": " ".join(match.group(1).split()).strip(" -")})
    return found


def _conversation(db: Session, user: User, conversation_id: str) -> Conversation:
    row = db.get(Conversation, conversation_id)
    if row is None or row.account_id != user.account_id or row.user_id != user.id:
        raise AppError("NOT_FOUND", "Conversation was not found.", 404)
    return row


def _document(db: Session, user: User, document_id: str) -> Document:
    document = db.get(Document, document_id)
    if document is None or document.account_id != user.account_id:
        raise AppError("NOT_FOUND", "Document was not found.", 404)
    return document


def _application(db: Session, user: User, application_id: str) -> Application:
    row = db.get(Application, application_id)
    if row is None or row.account_id != user.account_id:
        raise AppError("NOT_FOUND", "Application was not found.", 404)
    return row


def _default_application(db: Session, user: User) -> Application | None:
    return db.query(Application).filter_by(account_id=user.account_id, code="easidoc").one_or_none()


def _open_incident(db: Session, document_id: str) -> Incident | None:
    return (
        db.query(Incident)
        .filter(Incident.document_id == document_id, Incident.state.notin_(["resolved", "closed"]))
        .order_by(Incident.opened_at.desc())
        .first()
    )


def _messages(db: Session, conversation_id: str) -> list[ConversationMessage]:
    return (
        db.query(ConversationMessage)
        .filter(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.created_at.asc())
        .all()
    )


def _conversation_dict(db: Session, row: Conversation) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "application_id": row.application_id,
        "document_id": row.document_id,
        "incident_id": row.incident_id,
        "created_at": iso(row.created_at),
    }


def _message_dict(message: ConversationMessage) -> dict:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "citations": message.citations or [],
        "context": message.context or {},
        "created_at": iso(message.created_at),
    }


def _application_dict(application: Application | None) -> dict | None:
    if application is None:
        return None
    return {
        "id": application.id,
        "name": application.name,
        "code": application.code,
        "environment": application.environment,
        "label": application.label,
    }


def _incident_brief(incident: Incident) -> dict:
    return {
        "id": incident.id,
        "number": incident.number,
        "state": incident.state,
        "source": incident.source,
        "environment": incident.environment,
        "short_description": incident.short_description,
    }
