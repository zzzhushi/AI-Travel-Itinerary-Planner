from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.research_agent import run_research
from web.database import SessionLocal
from web.models import ResearchJob, ResearchOption


async def run_research_and_save(job_id: int) -> None:
    async with SessionLocal() as db:
        job = await _get_job(db, job_id)
        if job is None:
            return

        job.status = "running"
        await db.commit()

        parsed = {
            "country": job.project.country or "",
            "city": job.project.city or "",
            "activities": [job.activity_query],
        }

        rows, error = await run_research(parsed)

        if error:
            job.status = "error"
            job.error_message = error
            job.completed_at = datetime.now(timezone.utc)
            await db.commit()
            return

        for i, row in enumerate(rows):
            db.add(ResearchOption(
                job_id=job_id,
                option_name=row.get("option_name", ""),
                address=row.get("address"),
                location=row.get("location"),
                link=row.get("link"),
                user_rating=row.get("user_rating"),
                sort_order=i,
            ))

        job.status = "done"
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()


async def _get_job(db: AsyncSession, job_id: int) -> Optional[ResearchJob]:
    result = await db.execute(
        select(ResearchJob).where(ResearchJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        return None
    # Eagerly load project for country/city
    await db.refresh(job, ["project"])
    return job
