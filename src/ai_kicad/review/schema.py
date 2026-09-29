"""Typed, validated machine-readable AI review reports."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


SEVERITIES = {"info", "warning", "error"}
CONFIDENCE_LEVELS = {"low", "medium", "high"}
EVIDENCE_TYPES = {
    "pin_mapping",
    "deterministic_finding",
    "engineering_fact",
    "schematic_net",
    "component",
    "reconciliation",
    "board_summary",
}


class ReviewSchemaError(ValueError):
    """Raised when a review report violates the V0.5 contract."""


@dataclass(frozen=True)
class ReviewEvidence:
    id: str
    type: str
    reference: str = ""
    pin: str = ""
    net: str = ""
    rule: str = ""

    @classmethod
    def from_dict(cls, value: Any) -> "ReviewEvidence":
        if not isinstance(value, dict):
            raise ReviewSchemaError("each evidence item must be an object")
        allowed = {"id", "type", "reference", "pin", "net", "rule"}
        extra = set(value) - allowed
        if extra:
            raise ReviewSchemaError(f"evidence has unsupported fields: {', '.join(sorted(extra))}")
        if not isinstance(value.get("id"), str) or not value["id"].strip():
            raise ReviewSchemaError("evidence.id must be a non-empty string")
        evidence_type = value.get("type")
        if not isinstance(evidence_type, str) or evidence_type not in EVIDENCE_TYPES:
            raise ReviewSchemaError(f"evidence.type must be one of {', '.join(sorted(EVIDENCE_TYPES))}")
        fields = {}
        for name in ("reference", "pin", "net", "rule"):
            field = value.get(name, "")
            if not isinstance(field, str):
                raise ReviewSchemaError(f"evidence.{name} must be a string")
            fields[name] = field
        return cls(id=value["id"], type=value["type"], **fields)


@dataclass(frozen=True)
class ReviewFinding:
    severity: str
    claim: str
    evidence: tuple[ReviewEvidence, ...]
    reasoning: str
    confidence: str
    suggested_action: str
    evidence_level: str = "inferred"

    @classmethod
    def from_dict(cls, value: Any) -> "ReviewFinding":
        if not isinstance(value, dict):
            raise ReviewSchemaError("each finding must be an object")
        required = {
            "severity", "claim", "evidence", "reasoning", "confidence",
            "suggested_action", "evidence_level",
        }
        missing = required - set(value)
        extra = set(value) - required
        if missing:
            raise ReviewSchemaError(f"finding is missing fields: {', '.join(sorted(missing))}")
        if extra:
            raise ReviewSchemaError(f"finding has unsupported fields: {', '.join(sorted(extra))}")
        if not isinstance(value["severity"], str) or value["severity"] not in SEVERITIES:
            raise ReviewSchemaError(f"severity must be one of {', '.join(sorted(SEVERITIES))}")
        if not isinstance(value["confidence"], str) or value["confidence"] not in CONFIDENCE_LEVELS:
            raise ReviewSchemaError(f"confidence must be one of {', '.join(sorted(CONFIDENCE_LEVELS))}")
        if value["evidence_level"] != "inferred":
            raise ReviewSchemaError("AI review findings must have evidence_level 'inferred'")
        for name in ("claim", "reasoning", "suggested_action"):
            if not isinstance(value[name], str) or not value[name].strip():
                raise ReviewSchemaError(f"finding.{name} must be a non-empty string")
        if not isinstance(value["evidence"], list) or not value["evidence"]:
            raise ReviewSchemaError("finding.evidence must be a non-empty array")
        evidence = tuple(ReviewEvidence.from_dict(item) for item in value["evidence"])
        if len({item.id for item in evidence}) != len(evidence):
            raise ReviewSchemaError("finding evidence IDs must be unique")
        return cls(
            severity=value["severity"],
            claim=value["claim"],
            evidence=evidence,
            reasoning=value["reasoning"],
            confidence=value["confidence"],
            suggested_action=value["suggested_action"],
            evidence_level=value["evidence_level"],
        )


@dataclass(frozen=True)
class ReviewReport:
    findings: tuple[ReviewFinding, ...]
    review_version: str = "0.5"

    @classmethod
    def from_dict(cls, value: Any) -> "ReviewReport":
        if not isinstance(value, dict):
            raise ReviewSchemaError("review report must be an object")
        if set(value) != {"review_version", "findings"}:
            raise ReviewSchemaError("review report must contain only review_version and findings")
        if value["review_version"] != "0.5":
            raise ReviewSchemaError("review_version must be '0.5'")
        if not isinstance(value["findings"], list):
            raise ReviewSchemaError("findings must be an array")
        return cls(
            review_version="0.5",
            findings=tuple(ReviewFinding.from_dict(item) for item in value["findings"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_version": self.review_version,
            "findings": [
                {
                    **{key: value for key, value in asdict(finding).items() if key != "evidence"},
                    "evidence": [asdict(evidence) for evidence in finding.evidence],
                }
                for finding in self.findings
            ],
        }


def review_json_schema() -> dict[str, Any]:
    """JSON Schema passed to providers that support constrained output."""
    evidence_properties = {
        "id": {"type": "string"},
        "type": {"type": "string", "enum": sorted(EVIDENCE_TYPES)},
        "reference": {"type": "string"},
        "pin": {"type": "string"},
        "net": {"type": "string"},
        "rule": {"type": "string"},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "review_version": {"type": "string", "enum": ["0.5"]},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "severity": {"type": "string", "enum": sorted(SEVERITIES)},
                        "claim": {"type": "string"},
                        "evidence": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": evidence_properties,
                                "required": ["id", "type", "reference", "pin", "net", "rule"],
                            },
                        },
                        "reasoning": {"type": "string"},
                        "confidence": {"type": "string", "enum": sorted(CONFIDENCE_LEVELS)},
                        "suggested_action": {"type": "string"},
                        "evidence_level": {"type": "string", "enum": ["inferred"]},
                    },
                    "required": [
                        "severity", "claim", "evidence", "reasoning", "confidence",
                        "suggested_action", "evidence_level",
                    ],
                },
            },
        },
        "required": ["review_version", "findings"],
    }


def validate_review_report(value: Any, review_input: dict[str, Any] | None = None) -> ReviewReport:
    """Validate the report contract and, when supplied, evidence references."""
    report = ReviewReport.from_dict(value)
    if review_input is None:
        return report
    available: dict[str, dict[str, str]] = {}
    if not isinstance(review_input, dict) or review_input.get("review_input_version") != "0.5":
        raise ReviewSchemaError("evidence validation requires a V0.5 review input")
    registry = review_input.get("evidence")
    if not isinstance(registry, list):
        raise ReviewSchemaError("review input must contain an explicit evidence registry array")
    for index, item in enumerate(registry):
        if not isinstance(item, dict):
            raise ReviewSchemaError(f"review input evidence[{index}] must be an object")
        evidence_id = item.get("id")
        evidence_type = item.get("type")
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise ReviewSchemaError(f"review input evidence[{index}].id must be a non-empty string")
        if not isinstance(evidence_type, str) or evidence_type not in EVIDENCE_TYPES:
            raise ReviewSchemaError(f"review input evidence[{index}].type is unsupported")
        if evidence_id in available:
            raise ReviewSchemaError(f"review input contains duplicate evidence ID: {evidence_id}")
        available[evidence_id] = {
            key: item[key] for key in ("type", "reference", "pin", "net", "rule")
            if isinstance(item.get(key), str)
        }
    for finding_index, finding in enumerate(report.findings):
        for evidence in finding.evidence:
            source = available.get(evidence.id)
            if source is None:
                raise ReviewSchemaError(
                    f"finding {finding_index} cites evidence ID absent from review input: {evidence.id}"
                )
            for field in ("type", "reference", "pin", "net", "rule"):
                cited_value = getattr(evidence, field)
                source_value = source.get(field, "")
                if cited_value and cited_value != source_value:
                    raise ReviewSchemaError(
                        f"finding {finding_index} evidence {evidence.id} has mismatched {field}"
                    )
    return report
