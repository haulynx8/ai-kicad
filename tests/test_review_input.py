"""Tests for the compact verified context supplied to review providers."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from ai_kicad.review.schema import validate_review_report
from ai_kicad.review_input import build_review_input


class ReviewInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        artifact = Path(__file__).resolve().parents[1] / "a-c_adapter-unified.json"
        cls.unified = json.loads(artifact.read_text(encoding="utf-8"))
        cls.context = build_review_input(cls.unified)

    def evidence(self, evidence_id):
        return next(item for item in self.context["evidence"] if item["id"] == evidence_id)

    def test_context_keeps_verified_schematic_and_reconciliation_evidence(self) -> None:
        self.assertEqual(self.context["review_input_version"], "0.5")
        cc1 = self.evidence("pin_mapping:J2:A5")
        self.assertEqual(cc1["function"], "CC1_A5")
        self.assertEqual(cc1["schematic_net"], "CC conn")
        self.assertEqual(cc1["status"], "matched")
        self.assertEqual(cc1["provenance"]["comparison"], "exact_net_name_match")
        self.assertEqual(cc1["id"], "pin_mapping:J2:A5")
        self.assertTrue(self.context["context"]["usb_engineering_facts"]["facts"]["cc_nets"])
        summary = self.evidence("reconciliation:summary")
        self.assertEqual(summary["net_mismatches"], 0)

    def test_context_is_compact_and_deterministic_findings_are_addressable(self) -> None:
        self.assertNotIn("geometry", self.context["context"])
        self.assertNotIn("layers", self.context["context"])
        self.assertIn("board_summary", self.context["context"])
        ids = [item["id"] for item in self.context["evidence"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(any(value.startswith("pin_mapping:") for value in ids))
        self.assertTrue(any(value.startswith("engineering_fact:") for value in ids))
        self.assertTrue(any(value.startswith("deterministic_finding:") for value in ids))
        cc_net = next(net for net in self.context["context"]["nets"] if net["name"] == "CC conn")
        self.assertEqual(set(cc_net["pin_mapping_ids"]), {
            "pin_mapping:J2:A5", "pin_mapping:J2:B5", "pin_mapping:U2:1", "pin_mapping:U2:2"
        })

    def test_citations_to_real_context_ids_validate(self) -> None:
        report = {
            "review_version": "0.5",
            "findings": [{
                "severity": "info",
                "claim": "The connector pin is connected to the controller CC net.",
                "evidence": [{
                    "id": "pin_mapping:J2:A5", "type": "pin_mapping", "reference": "J2", "pin": "A5"
                }],
                "reasoning": "This is a review statement based on the mapped function and net assignment.",
                "confidence": "high",
                "suggested_action": "No action; retain this mapping as a review reference.",
                "evidence_level": "inferred",
            }],
        }
        self.assertEqual(len(validate_review_report(report, self.context).findings), 1)

    def test_representative_adapter_review_fixture_matches_registry(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "adapter-review-v05.json"
        sample = json.loads(fixture.read_text(encoding="utf-8"))
        report = validate_review_report(sample["review"], self.context)
        self.assertEqual(len(report.findings), 2)
        self.assertTrue(all(item.evidence_level == "inferred" for item in report.findings))
        self.assertIn("cannot establish", report.findings[0].reasoning)

    def test_rejects_other_unified_schema_versions(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires a V0.4"):
            build_review_input({"schema_version": "0.3"})


if __name__ == "__main__":
    unittest.main()
