"""Inter-cluster travel matrix: cache-aware travel times between cluster anchors.

This is the coordination layer over the travel-time data plane. Given the
geographic clusters (from `workers.planner.clustering`) it:

  1. picks one representative *anchor* place_id per cluster,
  2. looks up each ordered anchor pair in `route_cache`,
  3. asks the `RoutesClient` only for the pairs that are missing or stale, then
     caches the results, and
  4. returns a per-mode K×K matrix keyed by (origin_cluster_id, dest_cluster_id).

Only inter-cluster legs are ever computed (K≈4–6), never the full N×N of
options — intra-cluster hops are treated as a short walk by downstream packing
and are intentionally absent from the matrix.

Degrades gracefully: with no client, no API key, or an API failure, it returns
whatever the cache already holds (possibly empty) and never raises.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol

from sqlalchemy.orm import Session

from src.db.queries import get_fresh_route_cache, upsert_route
from src.workers.planner.clustering import Cluster

logger = logging.getLogger(__name__)

# Modes computed by default. WALK and DRIVE each cost a single computeRouteMatrix
# call; TRANSIT is per-pair (K×(K−1) calls) and is therefore opt-in — pass it
# explicitly via `modes` when a caller actually needs transit times.
DEFAULT_MODES = ("walk", "drive")

# A travel leg as returned to callers: {"duration_seconds", "distance_meters"}.
Leg = dict


def _naive_utcnow() -> datetime:
    """Naive UTC now, matching the tz-naive DateTime columns in route_cache."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class _RoutesClient(Protocol):
    """Structural type for RoutesClient / MockRoutesClient (duck-typed)."""

    def compute_matrix(
        self, origin_place_ids: list[str], dest_place_ids: list[str], mode: str
    ) -> Optional[dict[tuple[str, str], Optional[dict]]]:
        ...


@dataclass(frozen=True)
class ClusterAnchor:
    """A cluster paired with the single place_id used to route to/from it."""

    cluster_id: int
    place_id: str


def select_anchors(
    clusters: list[Cluster],
    place_id_by_option: dict[int, Optional[str]],
    rating_by_option: Optional[dict[int, Optional[float]]] = None,
) -> list[ClusterAnchor]:
    """Pick one representative anchor place_id per cluster.

    The anchor is the cluster member with the highest google_rating that has a
    non-null place_id (ties break to the lowest option_id, since `member_ids` is
    pre-sorted ascending). Clusters whose members all lack a place_id yield no
    anchor and are simply skipped — they can't be routed.
    """
    ratings = rating_by_option or {}
    anchors: list[ClusterAnchor] = []
    for cluster in clusters:
        candidates = [
            oid for oid in cluster.member_ids if place_id_by_option.get(oid)
        ]
        if not candidates:
            continue
        # max() is stable, and member_ids is sorted ascending, so equal ratings
        # resolve to the lowest option_id.
        best = max(
            candidates,
            key=lambda oid: ratings.get(oid) if ratings.get(oid) is not None else -1.0,
        )
        anchors.append(ClusterAnchor(cluster.cluster_id, place_id_by_option[best]))
    return anchors


def compute_inter_cluster_matrix(
    session: Session,
    anchors: list[ClusterAnchor],
    routes_client: Optional[_RoutesClient],
    *,
    modes: tuple[str, ...] = DEFAULT_MODES,
    now: Optional[datetime] = None,
) -> dict[str, dict[tuple[int, int], Leg]]:
    """Compute the cached inter-cluster travel matrix for the given anchors.

    Returns ``{mode: {(origin_cluster_id, dest_cluster_id): leg}}`` where a leg
    is ``{"duration_seconds", "distance_meters"}``. Unreachable pairs and
    same-cluster pairs are omitted. Fresh cache hits never touch the API; only
    missing/stale pairs trigger a (per-mode) RoutesClient call, whose results are
    then cached.
    """
    now = now or _naive_utcnow()

    # De-dupe by place_id while keeping the cluster mapping (two options sharing a
    # place_id would otherwise create a self-pair); first cluster wins.
    cid_by_place: dict[str, int] = {}
    for anchor in anchors:
        if anchor.place_id:
            cid_by_place.setdefault(anchor.place_id, anchor.cluster_id)
    place_ids = list(cid_by_place)

    matrix: dict[str, dict[tuple[int, int], Leg]] = {}
    for mode in modes:
        legs = _legs_for_mode(session, place_ids, mode, routes_client, now)
        mode_matrix: dict[tuple[int, int], Leg] = {}
        for (origin, dest), leg in legs.items():
            if leg and leg.get("duration_seconds") is not None:
                mode_matrix[(cid_by_place[origin], cid_by_place[dest])] = leg
        matrix[mode] = mode_matrix
    return matrix


def _legs_for_mode(
    session: Session,
    place_ids: list[str],
    mode: str,
    routes_client: Optional[_RoutesClient],
    now: datetime,
) -> dict[tuple[str, str], Optional[Leg]]:
    """Resolve every ordered anchor pair for one mode (cache first, then API)."""
    fresh = get_fresh_route_cache(session, mode, place_ids, now)
    needed = [(o, d) for o in place_ids for d in place_ids if o != d]
    legs: dict[tuple[str, str], Optional[Leg]] = {
        pair: _row_to_leg(fresh[pair]) for pair in needed if pair in fresh
    }

    missing = [pair for pair in needed if pair not in fresh]
    if not missing or routes_client is None:
        return legs  # everything fresh, or nothing we can do without a client

    try:
        fetched = routes_client.compute_matrix(place_ids, place_ids, mode)
    except Exception:
        logger.warning("Routes client raised computing %s matrix", mode, exc_info=True)
        return legs
    if fetched is None:
        # Hard API failure — keep the cache we have, fall back to estimates upstream.
        logger.info("Routes %s matrix unavailable; using cached/estimated times", mode)
        return legs

    for (origin, dest), leg in fetched.items():
        upsert_route(session, origin, dest, mode, leg, now)
        legs[(origin, dest)] = leg
    session.commit()
    return legs


def _row_to_leg(row) -> Optional[Leg]:
    """Convert a RouteCache row to a leg dict, or None for a cached unreachable."""
    if row.duration_seconds is None:
        return None
    return {
        "duration_seconds": row.duration_seconds,
        "distance_meters": row.distance_meters,
    }
