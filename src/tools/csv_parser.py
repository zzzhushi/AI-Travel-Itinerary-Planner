"""Parse activity CSV: vague activity descriptions and optional preference level."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Any


class CsvParseError(Exception):
    """Raised when CSV is invalid or has no activity rows."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass
class ActivityRow:
    """One row from the activity CSV: vague description + preference 1-5."""

    activity: str
    preference: int  # 1-5, 5 = highest want to do

    def to_dict(self) -> dict[str, Any]:
        return {"activity": self.activity, "preference": self.preference}


def _normalize_preference(val: Any) -> int:
    """Convert preference to 1-5. Default 3 if missing or invalid."""
    if val is None or val == "":
        return 3
    if isinstance(val, int):
        return max(1, min(5, val))
    s = str(val).strip().lower()
    if s in ("1", "2", "3", "4", "5"):
        return int(s)
    if s in ("low", "1"):
        return 1
    if s in ("medium", "med", "2", "3"):
        return 3
    if s in ("high", "4", "5"):
        return 5
    return 3


def parse_activity_csv(content: bytes | str) -> list[ActivityRow]:
    """
    Parse CSV with activity descriptions and optional preference.
    Supports: single column (vague_activity), or two columns (activity, preference).
    At least one data row required. Encoding: UTF-8.
    """
    if isinstance(content, bytes):
        content = content.decode("utf-8")
    content = content.strip()
    if not content:
        raise CsvParseError("CSV file is empty.")

    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    if not rows:
        raise CsvParseError("CSV has no rows.")

    # Detect header: first row might be header if it looks like labels
    start = 0
    if len(rows) > 0:
        first = [c.strip().lower() for c in rows[0]]
        if first and first[0] in ("activity", "activities", "vague_activity", "description", "preference", "pref"):
            start = 1

    result: list[ActivityRow] = []
    for i, row in enumerate(rows[start:], start=start + 1):
        if not row or all(not c.strip() for c in row):
            continue
        # Single column: whole row is activity, preference default 3
        if len(row) == 1:
            activity = row[0].strip()
            if activity:
                result.append(ActivityRow(activity=activity, preference=3))
            continue
        # Two or more columns: first is activity, second (if present) is preference
        activity = row[0].strip()
        if not activity:
            continue
        pref = _normalize_preference(row[1].strip() if len(row) > 1 else 3)
        result.append(ActivityRow(activity=activity, preference=pref))

    if not result:
        raise CsvParseError("CSV has no valid activity rows.")

    return result
