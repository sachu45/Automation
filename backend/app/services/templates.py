from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.models import EmailAccount, Lead, Sequence, SequenceStep, Template, TemplateVersion
from app.schemas.content import (
    PreviewOut,
    SequenceIn,
    SequenceOut,
    SequenceStepOut,
    TemplateIn,
    TemplateOut,
    TemplateUpdate,
    TemplateVersionOut,
)
from app.services import audit
from app.services.settings import get_config
from app.services.template_engine import (
    SAMPLE_LEAD_CONTEXT,
    MissingVariablesError,
    TemplateSyntaxError,
    UnresolvedPlaceholderError,
    validate,
)


def _check(subject: str, body: str) -> list[str]:
    analysis = validate(subject, body)
    if analysis.errors:
        raise ValidationFailed("Template has syntax errors", code="template_syntax", details=analysis.errors)
    return analysis.variables


def current_version(db: Session, template_id: int) -> TemplateVersion | None:
    return db.scalar(
        select(TemplateVersion).where(TemplateVersion.template_id == template_id).order_by(TemplateVersion.version.desc()).limit(1)
    )


def to_out(db: Session, t: Template) -> TemplateOut:
    out = TemplateOut.model_validate(t)
    cur = current_version(db, t.id)
    out.current = TemplateVersionOut.model_validate(cur) if cur else None
    out.version_count = db.scalar(select(func.count()).select_from(TemplateVersion).where(TemplateVersion.template_id == t.id)) or 0
    return out


def get_template(db: Session, template_id: int) -> Template:
    t = db.get(Template, template_id)
    if t is None:
        raise NotFound("Template not found")
    return t


def create_template(db: Session, data: TemplateIn) -> Template:
    if db.scalar(select(Template).where(Template.name == data.name)):
        raise Conflict("A template with this name already exists")
    variables = _check(data.subject, data.body)
    t = Template(name=data.name, description=data.description)
    db.add(t)
    db.flush()
    db.add(
        TemplateVersion(
            template_id=t.id, version=1, subject=data.subject, body=data.body, format=data.format, variables=variables, note=data.note
        )
    )
    audit.record(db, "template.created", f"Template '{t.name}' created", entity_type="template", entity_id=t.id)
    db.flush()
    return t


def update_template(db: Session, template_id: int, data: TemplateUpdate) -> Template:
    t = get_template(db, template_id)
    fields = data.model_fields_set
    if "name" in fields and data.name and data.name != t.name:
        if db.scalar(select(Template).where(Template.name == data.name, Template.id != t.id)):
            raise Conflict("A template with this name already exists")
        t.name = data.name
    if "description" in fields:
        t.description = data.description
    if "archived" in fields and data.archived is not None:
        t.archived = data.archived
    cur = current_version(db, t.id)
    content_fields = {"subject", "body", "format"} & fields
    if content_fields and cur is not None:
        subject = data.subject if data.subject is not None else cur.subject
        body = data.body if data.body is not None else cur.body
        fmt = data.format if data.format is not None else cur.format
        if (subject, body, fmt) != (cur.subject, cur.body, cur.format):
            variables = _check(subject, body)
            # Versions are immutable: create a new one. Approved campaigns stay pinned to old versions.
            db.add(
                TemplateVersion(
                    template_id=t.id, version=cur.version + 1, subject=subject, body=body, format=fmt, variables=variables, note=data.note
                )
            )
            audit.record(db, "template.versioned", f"Template '{t.name}' v{cur.version + 1}", entity_type="template", entity_id=t.id)
    db.flush()
    return t


def delete_template(db: Session, template_id: int) -> None:
    from app.models import CampaignStep

    t = get_template(db, template_id)
    used_in_seq = db.scalar(select(func.count()).select_from(SequenceStep).where(SequenceStep.template_id == t.id))
    used_in_campaign = db.scalar(
        select(func.count())
        .select_from(CampaignStep)
        .join(TemplateVersion, TemplateVersion.id == CampaignStep.template_version_id)
        .where(TemplateVersion.template_id == t.id)
    )
    if used_in_seq or used_in_campaign:
        raise Conflict("Template is used by a sequence or campaign; archive it instead")
    audit.record(db, "template.deleted", f"Template '{t.name}' deleted", entity_type="template", entity_id=t.id)
    db.delete(t)


def preview(db: Session, subject: str, body: str, fmt: str, lead_id: int | None, account_id: int | None) -> PreviewOut:
    from app.services.sending import compose

    analysis = validate(subject, body)
    base = PreviewOut(
        subject=None,
        text=None,
        html=None,
        missing=[],
        errors=analysis.errors,
        variables=analysis.variables,
        unknown_variables=analysis.unknown,
        lead_id=lead_id,
        sample_data=lead_id is None,
    )
    if analysis.errors:
        return base
    cfg = get_config(db)
    account = db.get(EmailAccount, account_id) if account_id else db.scalar(select(EmailAccount).order_by(EmailAccount.id).limit(1))
    if account is None:
        account = EmailAccount(display_name=cfg.sender_name or "Your Name", email=str(cfg.default_sender_email or "you@example.com"))
    lead = db.get(Lead, lead_id) if lead_id else None
    if lead_id and lead is None:
        raise NotFound("Lead not found")
    if lead is None:
        from types import SimpleNamespace

        s = SAMPLE_LEAD_CONTEXT
        lead = SimpleNamespace(  # type: ignore[assignment]
            id=None,
            email=s["email"],
            first_name=s["first_name"],
            last_name=s["last_name"],
            full_name=s["full_name"],
            job_title=s["job_title"],
            industry=s["industry"],
            country=s["country"],
            custom_fields={},
            company=SimpleNamespace(name=s["company"], website=s["company_website"]),
        )
    # Strict pass to list missing variables, then a lenient pass so the preview still renders
    try:
        composed = compose(cfg=cfg, lead=lead, account=account, subject=subject, body=body, fmt=fmt, campaign_id=None)  # type: ignore[arg-type]
    except MissingVariablesError as exc:
        base.missing = exc.missing
        composed = compose(cfg=cfg, lead=lead, account=account, subject=subject, body=body, fmt=fmt, campaign_id=None, strict=False)  # type: ignore[arg-type]
    except (TemplateSyntaxError, UnresolvedPlaceholderError) as exc:
        base.errors.append(str(exc))
        return base
    base.subject, base.text, base.html = composed.rendered.subject, composed.rendered.text, composed.rendered.html
    return base


def missing_for_lead(db: Session, version: TemplateVersion, lead: Lead, account: EmailAccount) -> list[str]:
    from app.services.sending import compose

    cfg = get_config(db)
    try:
        compose(cfg=cfg, lead=lead, account=account, subject=version.subject, body=version.body, fmt=version.format, campaign_id=None)
        return []
    except MissingVariablesError as exc:
        return exc.missing
    except (TemplateSyntaxError, UnresolvedPlaceholderError) as exc:
        return [f"error: {exc}"]


# --- Sequences ---------------------------------------------------------------------------------


def sequence_out(db: Session, seq: Sequence) -> SequenceOut:
    from app.models import Campaign

    out = SequenceOut(
        id=seq.id,
        name=seq.name,
        description=seq.description,
        created_at=seq.created_at,
        steps=[
            SequenceStepOut(
                id=s.id,
                position=s.position,
                template_id=s.template_id,
                template_name=s.template.name if s.template else None,
                delay_days=s.delay_days,
                delay_hours=s.delay_hours,
                send_as_reply=s.send_as_reply,
            )
            for s in seq.steps
        ],
    )
    out.campaign_count = db.scalar(select(func.count()).select_from(Campaign).where(Campaign.sequence_id == seq.id)) or 0
    return out


def _validate_steps(db: Session, data: SequenceIn) -> None:
    for i, step in enumerate(data.steps, start=1):
        t = db.get(Template, step.template_id)
        if t is None:
            raise ValidationFailed(f"Step {i}: template not found")
        if t.archived:
            raise ValidationFailed(f"Step {i}: template '{t.name}' is archived")
        if i > 1 and step.delay_days == 0 and step.delay_hours == 0:
            raise ValidationFailed(f"Step {i}: follow-ups need a wait interval")


def save_sequence(db: Session, data: SequenceIn, sequence_id: int | None = None) -> Sequence:
    _validate_steps(db, data)
    if sequence_id is None:
        if db.scalar(select(Sequence).where(Sequence.name == data.name)):
            raise Conflict("A sequence with this name already exists")
        seq = Sequence(name=data.name, description=data.description)
        db.add(seq)
    else:
        found = db.get(Sequence, sequence_id)
        if found is None:
            raise NotFound("Sequence not found")
        seq = found
        if db.scalar(select(Sequence).where(Sequence.name == data.name, Sequence.id != seq.id)):
            raise Conflict("A sequence with this name already exists")
        seq.name, seq.description = data.name, data.description
        seq.steps.clear()
        db.flush()
    for i, step in enumerate(data.steps, start=1):
        seq.steps.append(
            SequenceStep(
                position=i,
                template_id=step.template_id,
                delay_days=0 if i == 1 else step.delay_days,
                delay_hours=0 if i == 1 else step.delay_hours,
                send_as_reply=step.send_as_reply,
            )
        )
    db.flush()
    audit.record(db, "sequence.saved", f"Sequence '{seq.name}' saved ({len(data.steps)} steps)", entity_type="sequence", entity_id=seq.id)
    return seq


def delete_sequence(db: Session, sequence_id: int) -> None:
    seq = db.get(Sequence, sequence_id)
    if seq is None:
        raise NotFound("Sequence not found")
    audit.record(db, "sequence.deleted", f"Sequence '{seq.name}' deleted", entity_type="sequence", entity_id=seq.id)
    db.delete(seq)
