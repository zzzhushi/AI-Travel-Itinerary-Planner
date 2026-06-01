from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from web.database import get_db
from web.models import ResearchOption
from web.templates import templates

router = APIRouter()

Db = Annotated[AsyncSession, Depends(get_db)]


@router.patch("/options/{option_id}/rating", response_class=HTMLResponse)
async def update_rating(
    request: Request,
    option_id: int,
    rating: Annotated[str, Form()],
    db: Db,
):
    result = await db.execute(select(ResearchOption).where(ResearchOption.id == option_id))
    option = result.scalar_one_or_none()
    if option is None:
        return HTMLResponse("", status_code=404)

    option.user_rating = rating.strip() or None
    await db.commit()

    return templates.TemplateResponse(
        "partials/rating_input.html",
        {"request": request, "option": option},
    )
