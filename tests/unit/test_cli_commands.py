"""Smoke tests for CLI rendering commands in src/cli.py.

Use the shared in-memory `session` fixture and rich's capture() to assert
rendered output without terminal interaction. A wide console avoids column
truncation so field values are assertable.
"""

from __future__ import annotations

import pytest
from rich.console import Console

import src.cli as cli
from src.cli import cmd_show_options
from src.db.queries import add_activity, create_trip, save_options, set_rating


@pytest.fixture
def wide_console(monkeypatch):
    c = Console(width=200)
    monkeypatch.setattr(cli, "console", c)
    return c


def test_show_options_renders_fields(session, wide_console):
    trip = create_trip(session, "Test", "Tokyo", 3)
    act = add_activity(session, trip.id, "ramen", "food", False)
    [opt] = save_options(session, act.id, [{"name": "Ichiran", "location": "Shinjuku"}])
    set_rating(session, opt.id, 4)

    with wide_console.capture() as cap:
        cmd_show_options(session, trip)
    out = cap.get()

    assert "ramen" in out       # activity title
    assert "Ichiran" in out     # option name
    assert "Shinjuku" in out    # researcher location column


def test_show_options_handles_no_options(session, wide_console):
    trip = create_trip(session, "Empty", "Tokyo", 3)
    with wide_console.capture() as cap:
        cmd_show_options(session, trip)
    assert "No options yet" in cap.get()
