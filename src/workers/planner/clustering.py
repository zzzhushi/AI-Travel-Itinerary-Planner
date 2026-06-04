"""Deterministic geographic clustering of options.

Foundation slice for the geo-aware planner (issue #29 / #71): groups nearby
options into clusters so the LLM can later plan by neighborhood and so we only
ever compute a small inter-cluster (K×K) travel matrix instead of an N×N one.

Clustering is pure geometry, done deterministically (no LLM, no network):

  * Two options are *linked* when the walking distance between them is within a
    threshold (default 15 minutes on foot). Distance is haversine — close enough
    at city scale and dependency-free.
  * Clusters are the connected components of that link graph (single-linkage):
    A-B and B-C linked puts A, B, C together even if A-C exceeds the threshold.
  * Each cluster is named after the modal `neighborhood` of its members, with a
    rating-based tie-break and a "Area near {name}" fallback.
  * Options with no coordinates can't be placed geometrically, so each becomes
    its own singleton cluster — the pipeline never crashes on missing lat/lng.

The core algorithm operates on lightweight `ClusterPoint` inputs (no DB, no
ScheduleItem dependency) so it is trivially unit-testable. `assign_clusters`
bridges the result back onto `ScheduleItem`s by option_id.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable, Optional

from src.workers.planner.types import ScheduleItem

# Typical pedestrian speed. ~5 km/h is the common planning assumption (Google
# Maps walking estimates sit near this). 15 min × 5 km/h = 1.25 km link radius.
WALK_SPEED_KMH = 5.0
DEFAULT_WALK_MINUTES = 15.0

# Mean Earth radius (km) used by the haversine formula.
_EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class ClusterPoint:
    """Minimal clustering input for one option.

    Carries just what clustering needs: identity (`option_id`, `name`),
    geometry (`latitude`/`longitude`, either may be None), and the naming hints
    (`neighborhood`, `google_rating`). Decoupled from ScheduleItem so the
    algorithm stays pure and DB-free.
    """

    option_id: int
    name: str
    latitude: Optional[float]
    longitude: Optional[float]
    neighborhood: Optional[str] = None
    google_rating: Optional[float] = None

    @property
    def has_coords(self) -> bool:
        return self.latitude is not None and self.longitude is not None


@dataclass(frozen=True)
class Cluster:
    """A formed cluster: a stable id, a display name, and its member option_ids."""

    cluster_id: int
    name: str
    member_ids: tuple[int, ...]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two lat/lng points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def walk_threshold_km(
    walk_minutes: float = DEFAULT_WALK_MINUTES,
    walk_speed_kmh: float = WALK_SPEED_KMH,
) -> float:
    """Link radius in km for a given walk time and speed (default 15 min → 1.25 km)."""
    return walk_speed_kmh * (walk_minutes / 60.0)


def cluster_points(
    points: Iterable[ClusterPoint],
    *,
    walk_minutes: float = DEFAULT_WALK_MINUTES,
    walk_speed_kmh: float = WALK_SPEED_KMH,
) -> list[Cluster]:
    """Group points into geographic clusters (connected components / single-linkage).

    Points within `walk_minutes` on foot of each other are linked; clusters are
    the connected components of that link graph. Points without coordinates each
    become their own singleton cluster.

    Cluster ids are assigned deterministically (clusters ordered by their lowest
    member option_id), so repeated runs over the same input are stable.
    """
    threshold = walk_threshold_km(walk_minutes, walk_speed_kmh)
    points = list(points)

    geo = [p for p in points if p.has_coords]
    no_coords = [p for p in points if not p.has_coords]

    # Union-find over the geo-located points: union any pair within the threshold,
    # then read off connected components. O(n²) pair scan is fine at our scale
    # (tens of rated options per trip).
    parent = list(range(len(geo)))

    def find(x: int) -> int:
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:  # path compression
            parent[x], x = root, parent[x]
        return root

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for i in range(len(geo)):
        for j in range(i + 1, len(geo)):
            if haversine_km(
                geo[i].latitude, geo[i].longitude,
                geo[j].latitude, geo[j].longitude,
            ) <= threshold:
                union(i, j)

    components: dict[int, list[ClusterPoint]] = defaultdict(list)
    for idx, point in enumerate(geo):
        components[find(idx)].append(point)

    # Each component is a cluster; each coordinate-less point is its own singleton.
    member_groups = list(components.values()) + [[p] for p in no_coords]

    # Deterministic ordering: clusters by lowest member option_id, members within
    # a cluster by option_id (so naming tie-breaks resolve stably).
    for group in member_groups:
        group.sort(key=lambda p: p.option_id)
    member_groups.sort(key=lambda g: g[0].option_id)

    return [
        Cluster(
            cluster_id=cid,
            name=_name_cluster(members),
            member_ids=tuple(p.option_id for p in members),
        )
        for cid, members in enumerate(member_groups)
    ]


def assign_clusters(
    items: Iterable[ScheduleItem],
    clusters: Iterable[Cluster],
) -> None:
    """Stamp cluster_id / cluster_name onto ScheduleItems in place, by option_id.

    Items whose option_id is not in any cluster are left untouched (cluster_id /
    cluster_name stay None).
    """
    label: dict[int, Cluster] = {
        option_id: cluster
        for cluster in clusters
        for option_id in cluster.member_ids
    }
    for item in items:
        cluster = label.get(item.option_id)
        if cluster is not None:
            item.cluster_id = cluster.cluster_id
            item.cluster_name = cluster.name


def _name_cluster(members: list[ClusterPoint]) -> str:
    """Name a cluster after the modal neighborhood of its members.

    - Modal neighborhood among members that have one.
    - Tie between neighborhoods → the neighborhood of the highest-google_rating
      member among the tied ones.
    - No member has a neighborhood → "Area near {top member}", where the top
      member is the highest-rated (ties broken by lowest option_id, since
      `members` is pre-sorted by the caller).
    """
    counts = Counter(m.neighborhood for m in members if m.neighborhood)
    if counts:
        top_count = counts.most_common(1)[0][1]
        tied = {name for name, c in counts.items() if c == top_count}
        if len(tied) == 1:
            return next(iter(tied))
        best = _top_member(m for m in members if m.neighborhood in tied)
        return best.neighborhood

    return f"Area near {_top_member(members).name}"


def _top_member(members: Iterable[ClusterPoint]) -> ClusterPoint:
    """Highest-google_rating member; ties go to the first (lowest option_id)."""
    return max(
        members,
        key=lambda m: m.google_rating if m.google_rating is not None else -1.0,
    )
