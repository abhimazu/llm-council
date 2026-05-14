"""Tests for the Stage-2 ranking parser.

Specifically targets the regression class that produced user-issues
#27, #113, and #156: the parser silently produced empty rankings
when the model deviated from the prescribed format. The new parser
returns a parse_status that distinguishes ok / partial / parse_error.
"""
import os
os.environ.setdefault("OPENROUTER_API_KEY", "test-key-for-import-only")

from backend.council import parse_ranking_from_text


class TestCleanParse:
    def test_three_responses_clean(self):
        text = (
            "Some prose evaluating each one...\n"
            "FINAL RANKING:\n"
            "1. Response B\n"
            "2. Response A\n"
            "3. Response C"
        )
        parsed, status, reason = parse_ranking_from_text(text, expected_n=3)
        assert parsed == ["Response B", "Response A", "Response C"]
        assert status == "ok"
        assert reason == ""

    def test_two_responses_clean(self):
        text = (
            "FINAL RANKING:\n"
            "1. Response A\n"
            "2. Response B"
        )
        parsed, status, _ = parse_ranking_from_text(text, expected_n=2)
        assert parsed == ["Response A", "Response B"]
        assert status == "ok"


class TestPartialParse:
    """Triggered live in 05_paid_verification.md when output was
    truncated by max_tokens mid-list (B1+B2)."""

    def test_truncated_mid_list(self):
        text = "FINAL RANKING:\n1. Response B"
        parsed, status, reason = parse_ranking_from_text(text, expected_n=2)
        assert parsed == ["Response B"]
        assert status == "partial"
        assert "1 of 2" in reason

    def test_one_of_three(self):
        text = "FINAL RANKING:\n1. Response C"
        parsed, status, _ = parse_ranking_from_text(text, expected_n=3)
        assert status == "partial"
        assert parsed == ["Response C"]


class TestParseError:
    def test_no_heading_no_labels(self):
        parsed, status, reason = parse_ranking_from_text(
            "I cannot rank these responses.", expected_n=3,
        )
        assert parsed == []
        assert status == "parse_error"
        assert "FINAL RANKING heading missing" in reason

    def test_heading_present_but_empty(self):
        parsed, status, _ = parse_ranking_from_text(
            "FINAL RANKING:\n(no responses to rank)", expected_n=2,
        )
        assert status == "parse_error"
        assert parsed == []


class TestTolerantFallback:
    """The parser is deliberately permissive: extract Response X tokens
    even when the heading is missing. This was the original code's
    behavior; we preserve it but flag the parse status appropriately."""

    def test_no_heading_but_response_tokens_in_order(self):
        text = "Response B is the best, then Response A, then Response C."
        parsed, status, _ = parse_ranking_from_text(text, expected_n=3)
        # Tolerant: finds tokens in order
        assert parsed == ["Response B", "Response A", "Response C"]
        # Even though it parsed cleanly, no heading is a soft signal
        # that the model didn't follow the format. Current behavior
        # is to still mark this 'ok' if the count matches; tightening
        # this is an eval-framework concern, not a refactor concern.
        assert status == "ok"


class TestDuplicateLabels:
    """Some models repeat labels mid-list. The parser dedupes while
    preserving order."""

    def test_dedup_preserves_first_occurrence_order(self):
        text = (
            "FINAL RANKING:\n"
            "1. Response A\n"
            "2. Response B\n"
            "3. Response A\n"  # mistake
        )
        parsed, status, reason = parse_ranking_from_text(text, expected_n=3)
        # Deduped to [A, B]; flagged partial because it's only 2 of 3.
        assert parsed == ["Response A", "Response B"]
        assert status == "partial"


class TestTruncationOfExcessLabels:
    """If model produced more ranks than expected (label collision after
    dedupe), parser truncates to expected_n and reports ok."""

    def test_truncation_to_expected(self):
        text = (
            "FINAL RANKING:\n"
            "1. Response A\n"
            "2. Response B\n"
            "3. Response C\n"
            "4. Response D"
        )
        parsed, status, _ = parse_ranking_from_text(text, expected_n=3)
        assert parsed == ["Response A", "Response B", "Response C"]
        assert status == "ok"
