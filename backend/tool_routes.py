"""Read-only material checks, with customer and project authorization."""
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from backend.access import accessible_project_or_404, require_project_permission
from backend.chat_routes import customer
from backend.db import get_session
from backend.tools.materials import check_brief, preview

router = APIRouter(prefix="/api", tags=["implementation-tools"])


class BriefRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft: dict = Field(max_length=30)


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csv_text: str = Field(min_length=1, max_length=500000)


@router.post("/project-briefs/check")
async def check(payload: BriefRequest, user=Depends(customer)):
    return {"data": check_brief(payload.draft)}


@router.post("/projects/{project_id}/imports/preview")
async def import_preview(project_id: UUID, payload: PreviewRequest, user=Depends(customer), session=Depends(get_session)):
    project = await accessible_project_or_404(session, str(project_id), user)
    await require_project_permission(session, project, user, "import.validate")
    return {"data": await preview(session, project, payload.csv_text)}
