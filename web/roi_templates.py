"""
ROI templates: a company's assumptions for working out the ROI of automating a process (cost per hour, cost to
build and run an automation, the period, hours per full-time person) and which indicators it reports. A project
made with one gets a copy of it (roi.json in its folder, see roi.py), which its author can still change.
"""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlmodel import func, select

from roi import RoiAssumptions
from web.auth import active_user, current_user
from web.db import RoiTemplate, User, session, utcnow

router = APIRouter(prefix="/api/roi-templates", tags=["roi"])
MAX_PER_USER = 50


class RoiTemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    assumptions: RoiAssumptions


class RoiTemplateOut(RoiTemplateIn):
    id: str
    created_at: datetime
    updated_at: datetime


def _out(template: RoiTemplate) -> RoiTemplateOut:
    return RoiTemplateOut(id=template.id, name=template.name, created_at=template.created_at,
                          updated_at=template.updated_at,
                          assumptions=RoiAssumptions.model_validate_json(template.assumptions))


def own_roi_template(request: Request, template_id: str, user: User) -> RoiTemplate:
    """The ROI template, if it exists and belongs to `user`; otherwise 404."""
    with session(request.app.state.engine) as db:
        template = db.get(RoiTemplate, template_id)
    if template is None or template.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ROI template not found.")
    return template


@router.get("", response_model=list[RoiTemplateOut])
def list_templates(request: Request, user: User = Depends(current_user)):
    with session(request.app.state.engine) as db:
        templates = db.exec(select(RoiTemplate).where(RoiTemplate.user_id == user.id)
                            .order_by(RoiTemplate.name)).all()
    return [_out(t) for t in templates]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=RoiTemplateOut)
def create_template(body: RoiTemplateIn, request: Request, user: User = Depends(active_user)):
    with session(request.app.state.engine) as db:
        count = db.exec(select(func.count()).select_from(RoiTemplate).where(RoiTemplate.user_id == user.id)).one()
        if count >= MAX_PER_USER:
            raise HTTPException(status.HTTP_409_CONFLICT, f"You can have up to {MAX_PER_USER} ROI templates.")
        template = RoiTemplate(user_id=user.id, name=body.name.strip(), assumptions=body.assumptions.model_dump_json())
        db.add(template)
        db.commit()
    return _out(template)


@router.get("/{template_id}", response_model=RoiTemplateOut)
def get_template(template_id: str, request: Request, user: User = Depends(current_user)):
    return _out(own_roi_template(request, template_id, user))


@router.put("/{template_id}", response_model=RoiTemplateOut)
def update_template(template_id: str, body: RoiTemplateIn, request: Request, user: User = Depends(current_user)):
    """Changes the template for projects made with it from now on; earlier projects keep their copy."""
    own_roi_template(request, template_id, user)
    with session(request.app.state.engine) as db:
        template = db.get(RoiTemplate, template_id)
        template.name, template.assumptions = body.name.strip(), body.assumptions.model_dump_json()
        template.updated_at = utcnow()
        db.add(template)
        db.commit()
    return _out(template)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: str, request: Request, user: User = Depends(current_user)):
    own_roi_template(request, template_id, user)
    with session(request.app.state.engine) as db:
        db.delete(db.get(RoiTemplate, template_id))
        db.commit()
