"""Unit tests for CLI input helpers in src/cli.py.

builtins.input is patched so no actual terminal interaction occurs.
"""

from unittest.mock import patch

import pytest

from src.cli import prompt, prompt_int


class TestPromptInt:
    def test_valid_int_in_range(self):
        # Input: "5" within [1, 10] → returns int 5 immediately
        with patch("builtins.input", return_value="5"):
            result = prompt_int("Enter:", min_val=1, max_val=10)
        assert result == 5

    def test_non_integer_re_prompts(self):
        # Input: "abc" (invalid) then "3" (valid) → re-prompts once, returns 3
        with patch("builtins.input", side_effect=["abc", "3"]):
            result = prompt_int("Enter:", min_val=1, max_val=10)
        assert result == 3

    def test_out_of_range_low_re_prompts(self):
        # Input: "0" (below min=1) then "2" (valid) → re-prompts, returns 2
        with patch("builtins.input", side_effect=["0", "2"]):
            result = prompt_int("Enter:", min_val=1, max_val=10)
        assert result == 2

    def test_out_of_range_high_re_prompts(self):
        # Input: "999" (above max=10) then "7" (valid) → re-prompts, returns 7
        with patch("builtins.input", side_effect=["999", "7"]):
            result = prompt_int("Enter:", min_val=1, max_val=10)
        assert result == 7

    def test_empty_string_with_default_returns_default(self):
        # Input: "" (empty) with default=5 → returns 5 without re-prompting
        with patch("builtins.input", return_value=""):
            result = prompt_int("Enter:", min_val=1, max_val=10, default=5)
        assert result == 5

    def test_empty_string_without_default_returns_none(self):
        # Input: "" with no default → returns None (skip behavior shown as "Enter to skip" in prompt)
        with patch("builtins.input", return_value=""):
            result = prompt_int("Enter:", min_val=1, max_val=10)
        assert result is None

    def test_boundary_values_accepted(self):
        # min and max themselves are valid
        with patch("builtins.input", return_value="1"):
            assert prompt_int("Enter:", min_val=1, max_val=5) == 1
        with patch("builtins.input", return_value="5"):
            assert prompt_int("Enter:", min_val=1, max_val=5) == 5


class TestPrompt:
    def test_returns_stripped_input(self):
        # Input: "  Tokyo  " → output: "Tokyo" (whitespace stripped)
        with patch("builtins.input", return_value="  Tokyo  "):
            result = prompt("Destination:")
        assert result == "Tokyo"

    def test_empty_input_returns_default(self):
        # Input: "" with default "Paris" → returns "Paris"
        with patch("builtins.input", return_value=""):
            result = prompt("Destination:", default="Paris")
        assert result == "Paris"

    def test_empty_input_no_default_returns_empty(self):
        # Input: "" with no default → returns ""
        with patch("builtins.input", return_value=""):
            result = prompt("Destination:")
        assert result == ""
