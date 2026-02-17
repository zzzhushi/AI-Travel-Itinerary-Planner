"""Travel time estimation between two locations. Stub implementation for itinerary agent."""

from __future__ import annotations

from typing import Literal

Mode = Literal["walking", "driving", "bus", "train", "transit"]


def estimate_travel_time(
    from_address: str,
    to_address: str,
    mode: Mode = "transit",
) -> int:
    """
    Return estimated travel time in minutes between two locations.
    Stub: returns a default based on mode (no external API).
    For production, integrate with Google Directions API or similar.
    """
    # Stub: return plausible defaults (minutes)
    defaults: dict[Mode, int] = {
        "walking": 15,
        "driving": 10,
        "bus": 20,
        "train": 15,
        "transit": 25,
    }
    return defaults.get(mode, 20)
