from __future__ import annotations
from datetime import datetime
from typing import Optional
from sqlalchemy import Integer, Text, ForeignKey, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from web.database import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[Optional[str]] = mapped_column(Text)
    city: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now())

    jobs: Mapped[list[ResearchJob]] = relationship("ResearchJob", back_populates="project", cascade="all, delete-orphan")


class ResearchJob(Base):
    __tablename__ = "research_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    activity_query: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship("Project", back_populates="jobs")
    options: Mapped[list[ResearchOption]] = relationship("ResearchOption", back_populates="job", cascade="all, delete-orphan", order_by="ResearchOption.sort_order")


class ResearchOption(Base):
    __tablename__ = "research_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(Integer, ForeignKey("research_jobs.id", ondelete="CASCADE"), nullable=False)
    option_name: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[Optional[str]] = mapped_column(Text)
    location: Mapped[Optional[str]] = mapped_column(Text)
    link: Mapped[Optional[str]] = mapped_column(Text)
    user_rating: Mapped[Optional[str]] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    job: Mapped[ResearchJob] = relationship("ResearchJob", back_populates="options")
