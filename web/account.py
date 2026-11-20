"""
A person's rights over their data (LGPD art. 18): see everything the site keeps about them, and delete
their account with all of it.
"""
import json
import shutil
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlmodel import delete, select

from roi import load_inputs
from web.auth import COOKIE, current_user
from web.db import ApiKey, CreditEntry, Purchase, RoiTemplate, Usage, Run, Template, User, session, utcnow

router = APIRouter(prefix="/api/account", tags=["account"])


class Confirmation(BaseModel):
    email: str  # typed again by the person, so the account isn't deleted by a slip


def _iso(value):
    return value.isoformat() if value else None


def _roi(request: Request, run: Run) -> dict | None:
    inputs = load_inputs(str(request.app.state.settings.runs_dir / run.id))
    return inputs.model_dump() if inputs else None


@router.get("/export")
def export_data(request: Request, user: User = Depends(current_user)):
    """Everything stored about the account, as JSON. Documents, recordings and templates are files; their
    names are listed here and the documents can be downloaded from each project."""
    with session(request.app.state.engine) as db:
        runs = db.exec(select(Run).where(Run.user_id == user.id).order_by(Run.created_at)).all()
        templates = db.exec(select(Template).where(Template.user_id == user.id)).all()
        entries = db.exec(select(CreditEntry).where(CreditEntry.user_id == user.id).order_by(CreditEntry.id)).all()
        purchases = db.exec(select(Purchase).where(Purchase.user_id == user.id).order_by(Purchase.created_at)).all()
        keys = db.exec(select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at)).all()
        usage = db.exec(select(Usage).where(Usage.user_id == user.id).order_by(Usage.created_at)).all()
        roi_templates = db.exec(select(RoiTemplate).where(RoiTemplate.user_id == user.id)).all()
    data = {
        "exported_at": _iso(utcnow()),
        "account": {"email": user.email, "name": user.name, "created_at": _iso(user.created_at),
                    "privacy_accepted_at": _iso(user.privacy_accepted_at), "privacy_version": user.privacy_version},
        "projects": [{"name": r.name, "recording": r.video_name, "process": r.process_name, "status": r.status,
                      "template": r.template_name, "language": r.language, "credits": r.credits,
                      "created_at": _iso(r.created_at), "finished_at": _iso(r.finished_at),
                      "upload_consent_at": _iso(r.consent_at), "recording_deleted_at": _iso(r.recording_deleted_at),
                      "roi": _roi(request, r)}
                     for r in runs],
        "templates": [{"name": t.name, "language": t.language, "created_at": _iso(t.created_at)} for t in templates],
        "roi_templates": [{"name": t.name, "assumptions": json.loads(t.assumptions), "created_at": _iso(t.created_at)}
                          for t in roi_templates],
        "credits": [{"amount": e.amount, "kind": e.kind, "note": e.note, "created_at": _iso(e.created_at)}
                    for e in entries],
        "ai_costs": [{"kind": u.kind, "provider": u.provider, "cost_usd": u.cost_usd, "tokens": u.tokens,
                      "created_at": _iso(u.created_at)} for u in usage],
        # Keys are stored encrypted and never shown in full, not even here.
        "api_keys": [{"provider": k.provider, "name": k.name, "hint": f"{k.prefix}…{k.last4}",
                      "created_at": _iso(k.created_at)} for k in keys],
        "purchases": [{"credits": p.credits, "price_cents": p.price_cents, "status": p.status,
                       "created_at": _iso(p.created_at), "paid_at": _iso(p.paid_at)} for p in purchases],
    }
    return Response(json.dumps(data, ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="pidi-my-data.json"'})


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(body: Confirmation, request: Request, response: Response, user: User = Depends(current_user)):
    """Deletes the account and everything in it: projects (recordings, documents), templates, credits."""
    if body.email.strip().lower() != user.email:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Type your account's email to confirm.")
    settings = request.app.state.settings
    with session(request.app.state.engine) as db:
        runs = db.exec(select(Run).where(Run.user_id == user.id)).all()
        if any(r.status == "running" for r in runs):
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "A project is being made right now; delete the account when it has finished.")
        templates = db.exec(select(Template).where(Template.user_id == user.id)).all()
        for model in (Run, Template, RoiTemplate, CreditEntry, Purchase, ApiKey, Usage):
            db.exec(delete(model).where(model.user_id == user.id))
        db.delete(db.get(User, user.id))
        db.commit()
    for run in runs:
        shutil.rmtree(settings.runs_dir / run.id, ignore_errors=True)
    for template in templates:
        shutil.rmtree(settings.templates_dir / template.id, ignore_errors=True)
    response.delete_cookie(COOKIE, path="/")
