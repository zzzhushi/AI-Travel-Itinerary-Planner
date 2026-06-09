"""SQLAlchemy ORM models for the itinerary planner."""

from __future__ import annotations

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
    UniqueConstraint,
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
    logistics: Mapped[list[Logistics]] = relationship(
        "Logistics", back_populates="trip", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Trip id={self.id} name={self.name!r} destination={self.destination!r}>"


class TripPreferences(Base):
    """Per-trip user preferences (interests, seed notes, etc.)."""

    __tablename__ = "trip_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[int] = mapped_column(Integer, ForeignKey("trips.id"), unique=True)
    # Travel style. All nullable; NULL = "no preference" = default behavior.
    pace: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)      # PACE_CHOICES
    budget: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)    # BUDGET_CHOICES
    # Per-trip day window in minutes-from-midnight; NULL falls back to the
    # planner's DAY_START_MINUTES / DAY_END_MINUTES defaults.
    day_start_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    day_end_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # JSON list of interest strings e.g. ["food", "nightlife", "temples"]
    interests: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    trip: Mapped[Trip] = relationship("Trip", back_populates="preferences")


# Trip preference choices (plain strings, validated in app logic — see
# src/db/queries.update_preferences and src/workers/preferences.Preferences).
PACE_CHOICES = ["relaxed", "balanced", "packed"]
BUDGET_CHOICES = ["budget", "mid_range", "splurge"]


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
    # Search string produced by the researcher for Places API lookup
    maps_search: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # One-sentence rationale from the researcher
    why: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Category from the researcher (food, culture, ...) — preferred over
    # Activity.category when scheduling; falls back to it when null.
    category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # Lat/lng stored for geo-aware scheduling
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # User rating 1–5 (null = unrated)
    user_rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # User-overridable typical visit length, in minutes. NULL = use the category
    # default (category_default_duration); a non-NULL value is the user's override
    # (edited in the UI). Per-placement overrides live on ScheduledItem.
    default_duration_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Fields populated by Places API enrichment (all nullable — enrichment is best-effort)
    place_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Neighborhood / sublocality (e.g. "Shinjuku") parsed from Places
    # addressComponents; used to name geographic clusters when scheduling.
    neighborhood: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    google_rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_level: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    phone_number: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    website: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    opening_hours: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    place_refreshed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
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
    # Real clock placement: start time in minutes from midnight (0–1439).
    start_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Per-placement duration override, in minutes. NULL = fall back to the
    # option's default_duration_minutes, then the category default.
    duration_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
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


# Trip logistics kinds and travel modes (plain strings, validated in app logic —
# see src/db/queries.add_logistics / update_logistics).
LOGISTICS_KINDS = ["arrival", "departure", "lodging"]
TRAVEL_MODES = ["flight", "train", "bus", "car", "ferry", "other"]


class Logistics(Base):
    """User-provided trip logistics: travel (arrival/departure) and lodging.

    One unified table for two kinds of fixed, user-entered facts that constrain
    scheduling (not researched/rated like Options):

    - **arrival / departure** (kind): a flight/train/bus (mode) that lands or
      leaves on a given `day_number` at `time_minutes`. Arrival compresses that
      day's usable start; departure caps its end (see workers/logistics.py
      build_day_window_fn). `transit_minutes` is the buffer to/from the point.
    - **lodging** (kind): a hotel covering `check_in_day..check_out_day`; the
      day's geographic home base (see services.trip_service.hotel_for_day).

    Location fields (place_id/lat/lng/maps_link) are shared and filled once by a
    best-effort Places geocode on save (place_refreshed_at gates re-lookups).
    """

    __tablename__ = "logistics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[int] = mapped_column(Integer, ForeignKey("trips.id"), nullable=False)
    # One of LOGISTICS_KINDS ("arrival" | "departure" | "lodging").
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # Travel only: one of TRAVEL_MODES. NULL for lodging.
    mode: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Free-text name: airport / station / hotel.
    label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Travel timing (arrival/departure). day_number is 1-based and independent of
    # Trip.start_date. time_minutes is local clock minutes-from-midnight.
    day_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    time_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    transit_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=0)
    # Lodging day-range (1-based). check_out convention is owned by hotel_for_day.
    check_in_day: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    check_out_day: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Location (geocoded; mirrors Option's Places fields; all best-effort).
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    place_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    maps_link: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    place_refreshed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    trip: Mapped[Trip] = relationship("Trip", back_populates="logistics")

    def __repr__(self) -> str:
        return f"<Logistics id={self.id} kind={self.kind!r} label={self.label!r}>"


# Travel modes used as the third key of route_cache. Kept lowercase and decoupled
# from the Google Routes API enum strings (WALK/DRIVE/TRANSIT), which the
# RoutesClient maps internally.
ROUTE_MODES = ["walk", "drive", "transit"]


class RouteCache(Base):
    """
    Cached travel time/distance between two places for one travel mode.

    Keyed by (origin_place_id, dest_place_id, mode). Only inter-cluster *anchor*
    pairs are ever stored — a K×K matrix (K≈4–6), never the full N×N of options.
    Intra-cluster travel is treated as a short walk and never stored here.

    Mirrors the Places staleness pattern (see ROUTES_STALE_DAYS /
    ROUTES_RETRY_DAYS in db/queries.py): refreshed_at gates re-fetching, and a
    row with NULL duration_seconds is a cached "no route found" / failed lookup
    that is retried only after the shorter retry window — preventing both
    repeated API calls within the staleness window and infinite retries.
    """

    __tablename__ = "route_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    origin_place_id: Mapped[str] = mapped_column(String(255), nullable=False)
    dest_place_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # One of ROUTE_MODES ("walk" | "drive" | "transit").
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    # NULL duration = a confirmed unreachable pair or a failed lookup (see above).
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    distance_meters: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "origin_place_id", "dest_place_id", "mode", name="uq_route_cache_key"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<RouteCache {self.origin_place_id}->{self.dest_place_id} "
            f"mode={self.mode} dur={self.duration_seconds}s>"
        )
