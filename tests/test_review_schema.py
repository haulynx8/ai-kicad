"""V0.5 review schema and evidence citation contract tests."""

from __future__ import annotations

import unittest

from ai_kicad.review.schema import ReviewSchemaError, validate_review_report


CONTEXT = {
    "review_input_version": "0.5",
    "evidence": [
        {"id": "pin_mapping:J2:A5", "type": "pin_mapping", "reference": "J2", "pin": "A5"}
    ],
    "context": {"pin_mappings": ["pin_mapping:J2:A5"]},
}


def finding(**updates):
    result = {
        "severity": "warning",
        "claim": "The CC1 path may need review.",
        "evidence": [{"id": "pin_mapping:J2:A5", "type": "pin_mapping", "reference": "J2", "pin": "A5"}],
        "reasoning": "The schematic assigns this pin to CC1, but the supplied context does not establish termination values.",
        "confidence": "medium",
        "suggested_action": "Check the applicable USB Type-C requirements and component values.",
        "evidence_level": "inferred",
    }
    result.update(updates)
    return result


class ReviewSchemaTests(unittest.TestCase):
    def test_accepts_machine_readable_inferred_report(self) -> None:
        report = validate_review_report(
            {"review_version": "0.5", "findings": [finding()]}, CONTEXT
        )
        serialized = report.to_dict()
        self.assertEqual(serialized["review_version"], "0.5")
        self.assertEqual(serialized["findings"][0]["evidence_level"], "inferred")
        self.assertEqual(serialized["findings"][0]["evidence"][0]["id"], "pin_mapping:J2:A5")

    def test_rejects_non_inferred_conclusion(self) -> None:
        with self.assertRaisesRegex(ReviewSchemaError, "evidence_level 'inferred'"):
            validate_review_report(
                {"review_version": "0.5", "findings": [finding(evidence_level="raw")]}, CONTEXT
            )

    def test_rejects_missing_or_invented_evidence(self) -> None:
        with self.assertRaisesRegex(ReviewSchemaError, "non-empty array"):
            validate_review_report(
                {"review_version": "0.5", "findings": [finding(evidence=[])]}, CONTEXT
            )
        invented = finding(evidence=[{
            "id": "pin_mapping:J2:A4", "type": "pin_mapping", "reference": "J2", "pin": "A4"
        }])
        with self.assertRaisesRegex(ReviewSchemaError, "absent from review input"):
            validate_review_report({"review_version": "0.5", "findings": [invented]}, CONTEXT)

    def test_rejects_citation_metadata_that_disagrees_with_source(self) -> None:
        mismatch = finding(evidence=[{
            "id": "pin_mapping:J2:A5", "type": "pin_mapping", "reference": "J2", "pin": "A4"
        }])
        with self.assertRaisesRegex(ReviewSchemaError, "mismatched pin"):
            validate_review_report({"review_version": "0.5", "findings": [mismatch]}, CONTEXT)

    def test_rejects_ambiguous_duplicate_evidence_ids_in_context(self) -> None:
        duplicate_context = {
            "review_input_version": "0.5",
            "evidence": [
                {"id": "record:1", "type": "component"},
                {"id": "record:1", "type": "pin_mapping"},
            ],
        }
        with self.assertRaisesRegex(ReviewSchemaError, "duplicate evidence ID"):
            validate_review_report(
                {"review_version": "0.5", "findings": []}, duplicate_context
            )

    def test_ids_outside_the_evidence_registry_cannot_be_cited(self) -> None:
        context_only = {
            "review_input_version": "0.5",
            "evidence": [],
            "context": {"external": {"id": "context:only", "type": "component"}},
        }
        cited = finding(evidence=[{
            "id": "context:only", "type": "component", "reference": "J2"
        }])
        with self.assertRaisesRegex(ReviewSchemaError, "absent from review input"):
            validate_review_report({"review_version": "0.5", "findings": [cited]}, context_only)

    def test_requires_explicit_evidence_registry(self) -> None:
        with self.assertRaisesRegex(ReviewSchemaError, "explicit evidence registry"):
            validate_review_report(
                {"review_version": "0.5", "findings": []}, {"review_input_version": "0.5"}
            )


if __name__ == "__main__":
    unittest.main()
