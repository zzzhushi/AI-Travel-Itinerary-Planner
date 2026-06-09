"""Unit tests for _extract_json and _extract_json_dict in src/agents/base.py.

These are pure string-parsing functions — no mocking needed.
"""

import pytest

import obslog
from obslog import sink as sink_registry
from obslog.testing import InMemorySink
from src.agents.base import _extract_json, _extract_json_dict, instrument_llm_call


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


class TestInstrumentLlmCall:
    """instrument_llm_call times the call and emits one llm_call event on exit."""

    @pytest.fixture
    def mem(self):
        s = InMemorySink()
        obslog.set_sink(s)
        yield s
        sink_registry.reset()

    def test_ok_emits_success_event_with_metrics(self, mem):
        with obslog.operation("op"):
            with instrument_llm_call("ResearcherAgent", "gemini-2.5-flash", "hello") as call:
                result = call.ok("a response")
        assert result == ("a response", "")
        ev = next(e for e in mem.log_records if e.kind == "llm_call")
        assert ev.fields["status"] == "ok"
        assert ev.fields["agent_name"] == "ResearcherAgent"
        assert ev.fields["model"] == "gemini-2.5-flash"
        assert ev.fields["prompt_chars"] == 5
        assert ev.fields["response_chars"] == len("a response")
        assert ev.fields["error"] is None
        assert ev.fields["latency_ms"] >= 0
        assert ev.blob == "a response"

    def test_failed_emits_error_event(self, mem):
        with obslog.operation("op"):
            with instrument_llm_call("PlannerAgent", "gemini-2.5-flash", "prompt") as call:
                result = call.failed("Agent error: boom")
        assert result == ("", "Agent error: boom")
        ev = next(e for e in mem.log_records if e.kind == "llm_call")
        assert ev.fields["status"] == "error"
        assert ev.fields["error"] == "Agent error: boom"
        assert ev.fields["response_chars"] == 0
        assert ev.blob is None

    def test_event_emitted_even_when_body_raises(self, mem):
        with pytest.raises(RuntimeError):
            with obslog.operation("op"):
                with instrument_llm_call("A", "m", "p"):
                    raise RuntimeError("kaboom")
        # The event still fires (cm __exit__ runs), recording the default ok/empty
        # outcome since neither ok() nor failed() was called.
        assert any(e.kind == "llm_call" for e in mem.log_records)
