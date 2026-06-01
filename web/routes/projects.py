from __future__ import annotations

import io
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.excel_export import rows_to_excel
from web.database import get_db
from web.models import Project, ResearchJob, ResearchOption
from web.templates import templates

router = APIRouter()

Db = Annotated[AsyncSession, Depends(get_db)]


@router.get("/", response_class=RedirectResponse)
async def root():
    return RedirectResponse("/projects", status_code=302)


@router.get("/projects", response_class=HTMLResponse)
async def list_projects(request: Request, db: Db):
    result = await db.execute(select(Project).order_by(Project.created_at.desc()))
    projects = result.scalars().all()
    return templates.TemplateResponse("projects_list.html", {"request": request, "projects": projects})


@router.post("/projects")
async def create_project(
    name: Annotated[str, Form()],
    country: Annotated[str, Form()] = "",
    city: Annotated[str, Form()] = "",
    db: Db = None,
):
    project = Project(name=name.strip(), country=country.strip() or None, city=city.strip() or None)
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return RedirectResponse(f"/projects/{project.id}", status_code=303)


@router.get("/projects/{project_id}", response_class=HTMLResponse)
async def project_detail(request: Request, project_id: int, db: Db):
    result = await db.execute(
        select(Project)
        .where(Project.id == project_id)
        .options(selectinload(Project.jobs).selectinload(ResearchJob.options))
    )
    project = result.scalar_one_or_none()
    if project is None:
        return HTMLResponse("Project not found", status_code=404)
    return templates.TemplateResponse("project_detail.html", {"request": request, "project": project})


@router.post("/projects/{project_id}/delete")
async def delete_project(project_id: int, db: Db):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project:
        await db.delete(project)
        await db.commit()
    return RedirectResponse("/projects", status_code=303)


@router.get("/projects/{project_id}/export")
async def export_project(project_id: int, db: Db):
    result = await db.execute(
        select(Project)
        .where(Project.id == project_id)
        .options(selectinload(Project.jobs).selectinload(ResearchJob.options))
    )
    project = result.scalar_one_or_none()
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    rows = []
    for job in project.jobs:
        for opt in job.options:
            rows.append({
                "activity_query": job.activity_query,
                "option_name": opt.option_name,
                "address": opt.address or "",
                "location": opt.location or "",
                "link": opt.link or "",
                "user_rating": opt.user_rating or "",
            })

    xlsx_bytes = rows_to_excel(rows)
    safe_name = project.name.replace(" ", "_")
    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.xlsx"'},
    )
