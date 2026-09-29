"""Unit tests for the Ollama adapter without requiring a running model."""

from __future__ import annotations

import json
import unittest
from urllib.error import URLError
from unittest.mock import patch

from ai_kicad.review.ollama import OllamaProvider
from ai_kicad.review.provider import ReviewProviderError


class FakeResponse:
    def __init__(self, value: dict) -> None:
        self.data = json.dumps(value).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.data


class OllamaProviderTests(unittest.TestCase):
    def test_reports_generation_timeout_separately_from_connection_failure(self) -> None:
        with patch(
            "ai_kicad.review.ollama.urlopen",
            side_effect=URLError(TimeoutError("timed out")),
        ):
            with self.assertRaisesRegex(ReviewProviderError, "review request timed out after 7s"):
                OllamaProvider("test-model", timeout_seconds=7).review({"evidence": [], "context": {}})

    def test_posts_constrained_read_only_review_and_validates_citations(self) -> None:
        context = {
            "review_input_version": "0.5",
            "evidence": [{
                "id": "pin_mapping:J2:A5", "type": "pin_mapping", "reference": "J2", "pin": "A5"
            }],
            "context": {"pin_mappings": ["pin_mapping:J2:A5"]},
        }
        report = {
            "review_version": "0.5",
            "findings": [{
                "severity": "warning",
                "claim": "The CC path warrants review.",
                "evidence": [{
                    "id": "pin_mapping:J2:A5", "type": "pin_mapping", "reference": "J2", "pin": "A5",
                    "net": "", "rule": "",
                }],
                "reasoning": "The supplied mapping does not establish termination requirements.",
                "confidence": "low",
                "suggested_action": "Check the governing USB Type-C requirements.",
                "evidence_level": "inferred",
            }],
        }
        response = FakeResponse({"message": {"content": json.dumps(report)}})

        with patch("ai_kicad.review.ollama.urlopen", return_value=response) as mocked_urlopen:
            result = OllamaProvider("test-model", base_url="localhost:11434").review(context)

        self.assertEqual(result.to_dict(), report)
        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "http://localhost:11434/api/chat")
        self.assertEqual(payload["model"], "test-model")
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["think"])
        self.assertEqual(payload["options"]["num_predict"], 1200)
        self.assertEqual(payload["format"]["properties"]["review_version"]["enum"], ["0.5"])
        self.assertIn("Do not propose or apply edits", payload["messages"][0]["content"])


if __name__ == "__main__":
    unittest.main()
