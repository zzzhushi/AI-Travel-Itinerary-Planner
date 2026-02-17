"""Integration test: session store + API flow (no real agent calls)."""

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.api.session_store import get_or_create_session


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def session_id() -> str:
    return get_or_create_session(None)


def test_create_session(client: TestClient) -> None:
    r = client.post("/api/session")
    assert r.status_code == 200
    data = r.json()
    assert "session_id" in data


def test_upload_trip(client: TestClient, session_id: str) -> None:
    trip_json = b'{"country": "South Korea", "city": "Seoul", "days": 5}'
    r = client.post(
        "/api/trip",
        files={"file": ("trip.json", trip_json, "application/json")},
        params={"session_id": session_id},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["country"] == "South Korea"
    assert data["city"] == "Seoul"
    assert data["days"] == 5


def test_upload_activities(client: TestClient, session_id: str) -> None:
    csv_content = b"activity,preference\nnight market,5\nsalt bread,4"
    r = client.post(
        "/api/activities",
        files={"file": ("activities.csv", csv_content, "text/csv")},
        params={"session_id": session_id},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["activity_count"] == 2


def test_upload_trip_invalid(client: TestClient, session_id: str) -> None:
    r = client.post(
        "/api/trip",
        files={"file": ("trip.json", b'{"city": "Seoul"}', "application/json")},
        params={"session_id": session_id},
    )
    assert r.status_code == 400


def test_get_itinerary_empty(client: TestClient) -> None:
    # Create session via API so it exists in store
    r0 = client.post("/api/session")
    assert r0.status_code == 200
    sid = r0.json()["session_id"]
    r = client.get("/api/itinerary", params={"session_id": sid})
    assert r.status_code == 200
    data = r.json()
    assert "itinerary" in data
    assert "alternatives" in data
