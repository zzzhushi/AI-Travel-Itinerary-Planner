"""Unit tests for _extract_json and _extract_json_dict in src/agents/base.py.

These are pure string-parsing functions — no mocking needed.
"""

import pytest

from src.agents.base import _extract_json, _extract_json_dict


class TestExtractJson:
    def test_plain_json_array(self):
        # Input: raw JSON array string → output: Python list
        text = '[{"name": "Eiffel Tower"}, {"name": "Louvre"}]'
        result = _extract_json(text)
        assert result == [{"name": "Eiffel Tower"}, {"name": "Louvre"}]

    def test_json_array_in_code_fence(self):
        # Input: JSON wrapped in ```json ... ``` → fence is stripped before parsing
        text = '```json\n[{"name": "A"}, {"name": "B"}]\n```'
        result = _extract_json(text)
        assert result == [{"name": "A"}, {"name": "B"}]

    def test_json_array_in_plain_code_fence(self):
        # Input: fence without language tag → still stripped
        text = "```\n[1, 2, 3]\n```"
        result = _extract_json(text)
        assert result == [1, 2, 3]

    def test_json_array_embedded_in_text(self):
        # Input: LLM preamble before the JSON → bracket scanning finds the array
        text = 'Here are your options:\n[{"name": "Place A"}]'
        result = _extract_json(text)
        assert result == [{"name": "Place A"}]

    def test_empty_string_returns_none(self):
        # Input: empty string → no JSON to extract
        assert _extract_json("") is None

    def test_plain_text_no_json_returns_none(self):
        # Input: non-JSON text → returns None, not raises
        assert _extract_json("Sorry, I could not find options.") is None

    def test_json_object_returns_none(self):
        # Input: JSON object (not array) → _extract_json expects array, returns None
        assert _extract_json('{"key": "value"}') is None

    def test_malformed_json_returns_none(self):
        # Input: broken JSON array → returns None rather than raising
        assert _extract_json('[{"name": "broken"') is None

    def test_empty_array(self):
        # Input: valid empty array → returns []
        assert _extract_json("[]") == []


class TestExtractJsonDict:
    def test_plain_json_object(self):
        # Input: raw JSON object string → output: Python dict
        text = '{"0": [{"name": "A"}], "1": [{"name": "B"}]}'
        result = _extract_json_dict(text)
        assert result == {"0": [{"name": "A"}], "1": [{"name": "B"}]}

    def test_json_object_in_code_fence(self):
        # Input: object wrapped in ```json ... ``` fence → stripped and parsed
        text = '```json\n{"key": "value"}\n```'
        result = _extract_json_dict(text)
        assert result == {"key": "value"}

    def test_json_object_embedded_in_text(self):
        # Input: preamble before the JSON object → brace scanning finds the object
        text = 'Results:\n{"0": [], "1": []}'
        result = _extract_json_dict(text)
        assert result == {"0": [], "1": []}

    def test_empty_string_returns_none(self):
        assert _extract_json_dict("") is None

    def test_json_array_returns_none(self):
        # Input: valid array → _extract_json_dict expects dict, returns None
        assert _extract_json_dict('[{"name": "A"}]') is None

    def test_malformed_json_returns_none(self):
        # Input: broken brace → returns None
        assert _extract_json_dict('{"key": "broken"') is None
