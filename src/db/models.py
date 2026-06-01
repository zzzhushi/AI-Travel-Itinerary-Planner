"""SQLAlchemy ORM models for the itinerary planner."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Trip(Base):
    """A top-level trip (e.g. 'Japan September 2026')."""

    __tablename__ = "trips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    destination: Mapped[str] = mapped_column(String(255), nullable=False)
    num_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    activities: Mapped[list[Activity]] = relationship(
        "Activity", back_populates="trip", cascade="all, delete-orphan"
    )
    scheduled_items: Mapped[list[ScheduledItem]] = relationship(
        "ScheduledItem", back_populates="trip", cascade="all, delete-orphan"
    )
    preferences: Mapped[Optional[TripPreferences]] = relationship(
        "TripPreferences", back_populates="trip", uselist=False, cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Trip id={self.id} name={self.name!r} destination={self.destination!r}>"


class TripPreferences(Base):
    """Per-trip user preferences (interests, seed notes, etc.)."""

    __tablename__ = "trip_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[int] = mapped_column(Integer, ForeignKey("trips.id"), unique=True)
    # JSON list of interest strings e.g. ["food", "nightlife", "temples"]
    interests: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    trip: Mapped[Trip] = relationship("Trip", back_populates="preferences")


# Activity categories (kept as plain strings for flexibility; validated in app logic)
ACTIVITY_CATEGORIES = [
    "food",
    "nightlife",
    "sightseeing",
    "shopping",
    "nature",
    "culture",
    "transport",
    "accommodation",
    "other",
]


class Activity(Base):
    """
    A user-supplied activity query for a trip.
    Can be vague ("ramen in Shinjuku") or specific ("Ichiran Ramen Shinjuku").
    """

    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[int] = mapped_column(Integer, ForeignKey("trips.id"), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # True when the user already has a specific place in mind (skip multi-option research)
    is_specific: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    researched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    trip: Mapped[Trip] = relationship("Trip", back_populates="activities")
    options: Mapped[list[Option]] = relationship(
        "Option", back_populates="activity", cascade="all, delete-orphan"
    )

    @property
    def research_hash(self) -> str:
        """Stable hash for idempotent research: same trip + query → same hash."""
        key = f"{self.trip_id}:{self.query.strip().lower()}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def __repr__(self) -> str:
        return f"<Activity id={self.id} query={self.query!r} specific={self.is_specific}>"


class Option(Base):
    """
    A concrete place / experience returned by the Researcher agent.
    Multiple options may exist per Activity (for vague queries).
    """

    __tablename__ = "options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    activity_id: Mapped[int] = mapped_column(Integer, ForeignKey("activities.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    maps_link: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Lat/lng stored for geo-aware scheduling
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # User rating 1–5 (null = unrated)
    user_rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Hash used to detect duplicate research runs (idempotency)
    research_hash: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    activity: Mapped[Activity] = relationship("Activity", back_populates="options")
    scheduled_item: Mapped[Optional[ScheduledItem]] = relationship(
        "ScheduledItem", back_populates="option", uselist=False
    )

    def __repr__(self) -> str:
        return f"<Option id={self.id} name={self.name!r} rating={self.user_rating}>"


class ScheduledItem(Base):
    """
    An option placed on the itinerary for a specific day (and optionally time).
    day_number is 1-based; null means unscheduled.
    When start_date is set on the Trip, the UI converts day_number to a real date.
    """

    __tablename__ = "scheduled_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[int] = mapped_column(Integer, ForeignKey("trips.id"), nullable=False)
    option_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("options.id"), unique=True, nullable=False
    )
    day_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Time slot: "morning" | "afternoon" | "evening" | null
    time_slot: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Locked items won't be moved by the planner or drag-drop
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    trip: Mapped[Trip] = relationship("Trip", back_populates="scheduled_items")
    option: Mapped[Option] = relationship("Option", back_populates="scheduled_item")

    def __repr__(self) -> str:
        return f"<ScheduledItem id={self.id} option_id={self.option_id} day={self.day_number} slot={self.time_slot}>"
