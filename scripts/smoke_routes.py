"""End-to-end smoke test for the #72 travel-time layer.

Runs in two modes controlled by --live / --offline (default: offline):

  python scripts/smoke_routes.py           # offline: HaversineRoutesClient, no API calls
  python scripts/smoke_routes.py --live    # live: real Routes API (needs GOOGLE_MAPS_API_KEY)

Both modes validate:
  - clustering + anchor selection on hardcoded Tokyo landmarks
  - compute_inter_cluster_matrix (full stack) against real Postgres
  - cache round-trip: second call makes ZERO client calls

The --live mode additionally validates the real Routes API request/response
contract for walk + drive + transit. Costs a few API calls per run.

Requires DATABASE_URL in .env; --live also needs GOOGLE_MAPS_API_KEY.
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv()


# Three Tokyo landmarks with hardcoded coordinates and real place_ids so the
# offline path needs zero API calls.
_LANDMARKS = [
    {
        "name": "Tokyo Tower",
        "place_id": "ChIJ3S-JXmauGGARoHIDezqHAAQ",
        "lat": 35.6586, "lng": 139.7454,
    },
    {
        "name": "Senso-ji Temple",
        "place_id": "ChIJ8T1GpMGOGGARoAEsNAKABA4",
        "lat": 35.7148, "lng": 139.7967,
    },
    {
        "name": "Shibuya Crossing",
        "place_id": "ChIJF3K_hsgbGGARVWJnTMXTmfU",
        "lat": 35.6595, "lng": 139.7004,
    },
]


class _CountingWrapper:
    def __init__(self, inner):
        self.inner = inner
        self.calls = 0

    def compute_matrix(self, origins, dests, mode):
        self.calls += 1
        return self.inner.compute_matrix(origins, dests, mode)

    def close(self):
        getattr(self.inner, "close", lambda: None)()


def _run_offline():
    from src.clients.haversine_routes_client import HaversineRoutesClient
    from src.services.travel import ClusterAnchor, compute_inter_cluster_matrix

    coords = {lm["place_id"]: (lm["lat"], lm["lng"]) for lm in _LANDMARKS}
    anchors = [ClusterAnchor(i, lm["place_id"]) for i, lm in enumerate(_LANDMARKS)]

    print("== offline: HaversineRoutesClient ==")
    client = HaversineRoutesClient(coords)
    result = client.compute_matrix(
        [a.place_id for a in anchors],
        [a.place_id for a in anchors],
        "walk",
    )
    for (o, d), leg in sorted(result.items()):
        on = next(lm["name"] for lm in _LANDMARKS if lm["place_id"] == o)
        dn = next(lm["name"] for lm in _LANDMARKS if lm["place_id"] == d)
        mins = leg["duration_seconds"] // 60
        print(f"  walk {on} -> {dn}: {mins} min  ({leg['distance_meters']} m straight-line)")

    print("\n== cache round-trip (SQLite in-memory) ==")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from src.db.models import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        c1 = _CountingWrapper(HaversineRoutesClient(coords))
        m1 = compute_inter_cluster_matrix(session, anchors, c1, modes=("walk", "drive"))
        print(f"  first run : {sum(len(v) for v in m1.values())} legs, {c1.calls} client calls")

        c2 = _CountingWrapper(HaversineRoutesClient(coords))
        m2 = compute_inter_cluster_matrix(session, anchors, c2, modes=("walk", "drive"))
        print(f"  second run: {sum(len(v) for v in m2.values())} legs, {c2.calls} client calls")

        assert c2.calls == 0, f"Expected 0 client calls on cached run, got {c2.calls}"
        print("  cache hit confirmed (0 client calls on repeat)")


def _run_live():
    from src.clients.places_client import places_client_from_env
    from src.clients.routes_client import routes_client_from_env
    from src.db.database import get_sync_session_factory
    from src.services.travel import ClusterAnchor, compute_inter_cluster_matrix

    if not os.getenv("GOOGLE_MAPS_API_KEY"):
        raise SystemExit("GOOGLE_MAPS_API_KEY not set — see .env.example")

    places_client = places_client_from_env()
    place_ids: list[str] = []
    print("== resolving place_ids via Places API ==")
    for lm in _LANDMARKS:
        hit = places_client.lookup(lm["name"])
        if not hit or not hit.get("place_id"):
            raise SystemExit(f"Could not resolve a place_id for {lm['name']!r}")
        print(f"  {lm['name']:25s} -> {hit['place_id']}")
        place_ids.append(hit["place_id"])
    places_client.close()

    routes = routes_client_from_env()
    print("\n== live Routes API (walk / drive / transit) ==")
    for mode in ("walk", "drive", "transit"):
        result = routes.compute_matrix(place_ids, place_ids, mode)
        if result is None:
            print(f"  {mode:7s}: API call FAILED (returned None)")
            continue
        legs = {k: v for k, v in result.items() if v}
        sample = next(iter(legs.items()), None)
        print(f"  {mode:7s}: {len(legs)} reachable legs; sample={sample}")
    routes.close()

    print("\n== cache round-trip (real Postgres) ==")
    anchors = [ClusterAnchor(i, pid) for i, pid in enumerate(place_ids)]
    factory = get_sync_session_factory()
    with factory() as session:
        c1 = _CountingWrapper(routes_client_from_env())
        m1 = compute_inter_cluster_matrix(session, anchors, c1, modes=("walk", "drive"))
        print(f"  first run : {sum(len(v) for v in m1.values())} legs, {c1.calls} API calls")
        c1.close()

        c2 = _CountingWrapper(routes_client_from_env())
        m2 = compute_inter_cluster_matrix(session, anchors, c2, modes=("walk", "drive"))
        print(f"  second run: {sum(len(v) for v in m2.values())} legs, {c2.calls} API calls")
        c2.close()

        assert c2.calls == 0, f"Expected 0 API calls on cached run, got {c2.calls}"
        print("  cache hit confirmed (0 API calls on repeat)")


if __name__ == "__main__":
    if "--live" in sys.argv:
        _run_live()
    else:
        _run_offline()
