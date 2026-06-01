from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from web.database import get_db
from web.models import Project, ResearchJob
from web.services.research_service import run_research_and_save
from web.templates import templates

router = APIRouter()

Db = Annotated[AsyncSession, Depends(get_db)]


@router.post("/projects/{project_id}/jobs", response_class=HTMLResponse)
async def create_job(
    request: Request,
    project_id: int,
    background_tasks: BackgroundTasks,
    activity: Annotated[str, Form()],
    db: Db,
):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    job = ResearchJob(project_id=project_id, activity_query=activity.strip(), status="pending")
    db.add(job)
    await db.commit()
    await db.refresh(job)

    background_tasks.add_task(run_research_and_save, job.id)

    return templates.TemplateResponse(
        "partials/job_pending.html",
        {"request": request, "job": job, "project": project},
    )


@router.get("/projects/{project_id}/jobs/{job_id}/status", response_class=HTMLResponse)
async def job_status(request: Request, project_id: int, job_id: int, db: Db):
    result = await db.execute(
        select(ResearchJob)
        .where(ResearchJob.id == job_id)
        .options(selectinload(ResearchJob.options))
    )
    job = result.scalar_one_or_none()
    if job is None:
        return HTMLResponse("", status_code=404)

    if job.status in ("pending", "running"):
        return templates.TemplateResponse(
            "partials/job_pending.html",
            {"request": request, "job": job, "project_id": project_id},
        )

    return templates.TemplateResponse(
        "partials/job_done.html",
        {"request": request, "job": job},
    )
