"""Build compact, evidence-addressable input for an AI review."""

from __future__ import annotations

from typing import Any

from ai_kicad.checks import analyze_model
from ai_kicad.engineering_facts import extract_usb_facts


def _reconciliation_findings(consistency: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    groups = (
        ("net_mismatches", "net_mismatch"),
        ("schematic_only_pins", "schematic_only_pin"),
        ("pcb_only_pads", "pcb_only_pad"),
        ("schematic_only_components", "schematic_only_component"),
        ("pcb_only_components", "pcb_only_component"),
        ("component_value_mismatches", "component_value_mismatch"),
    )
    for key, finding_type in groups:
        for index, source in enumerate(consistency.get(key, [])):
            item = dict(source)
            reference = str(item.get("reference", ""))
            pin = str(item.get("pin", item.get("pad", "")))
            item.update({
                "id": f"reconciliation:{finding_type}:{reference}:{pin}:{index}",
                "type": "reconciliation",
                "finding_type": finding_type,
            })
            findings.append(item)
    return findings


def _compact_usb_fact(group: str, source: dict[str, Any], index: int) -> dict[str, Any]:
    """Keep the fact's claimable values while relying on pin mappings for endpoints."""
    omit = {"pads", "endpoints", "positive_endpoints", "negative_endpoints"}
    return {
        "id": f"engineering_fact:{group}:{index}",
        "type": "engineering_fact",
        **{key: value for key, value in source.items() if key not in omit},
    }


def build_review_input(unified_model: dict[str, Any]) -> dict[str, Any]:
    """Select verified design facts and deterministic findings for review.

    The full PCB geometry and other raw KiCad implementation fields are omitted
    to keep model context focused. Every evidence-bearing record receives an ID
    the review schema can validate against.
    """
    if unified_model.get("schema_version") != "0.4":
        raise ValueError("review input requires a V0.4 unified design model")
    pcb = unified_model.get("pcb")
    if not isinstance(pcb, dict):
        raise ValueError("unified model is missing its embedded PCB model")

    usb_facts = extract_usb_facts(pcb)
    facts = {}
    for group, entries in usb_facts.get("usb", {}).items():
        facts[group] = [
            _compact_usb_fact(group, entry, index)
            for index, entry in enumerate(entries)
        ]

    components = []
    for component in unified_model.get("components", []):
        item = dict(component)
        item["id"] = f"component:{component['reference']}"
        item["type"] = "component"
        components.append(item)

    pin_mappings = []
    for mapping in unified_model.get("pin_mappings", []):
        source_provenance = mapping.get("provenance", {})
        item = {
            "id": f"pin_mapping:{mapping['reference']}:{mapping['pin']}",
            "type": "pin_mapping",
            "reference": mapping["reference"],
            "pin": mapping["pin"],
            "function": mapping.get("function", ""),
            "electrical_type": mapping.get("electrical_type", ""),
            "schematic_net": mapping.get("schematic_net", ""),
            "pcb_net": mapping.get("pcb_net", ""),
            "status": mapping.get("status", ""),
            "provenance": {
                "sources": [
                    source_provenance.get("schematic", {}).get("source", ""),
                    source_provenance.get("pcb", {}).get("source", ""),
                ],
                "comparison": source_provenance.get("comparison", ""),
            },
        }
        pin_mappings.append(item)

    schematic_nets = []
    for index, net in enumerate(unified_model.get("schematic_nets", [])):
        net_id = str(net.get("code") or f"{net.get('name', 'unnamed')}:{index}")
        schematic_nets.append({
            "id": f"schematic_net:{net_id}",
            "type": "schematic_net",
            "name": net.get("name", ""),
            "net_type": net.get("net_type", ""),
            "pin_mapping_ids": [
                f"pin_mapping:{pin['reference']}:{pin['number']}"
                for pin in net.get("pins", [])
            ],
        })

    deterministic = analyze_model(pcb)
    deterministic_findings = []
    for index, finding in enumerate(deterministic.get("findings", [])):
        item = dict(finding)
        item["id"] = f"deterministic_finding:{finding['rule']}:{index}"
        item["type"] = "deterministic_finding"
        deterministic_findings.append(item)

    consistency = unified_model.get("consistency", {})
    reconciliation_findings = _reconciliation_findings(consistency)
    board_summary = {
        "id": "board_summary:counts",
        "type": "board_summary",
        "name": pcb.get("source", {}).get("board", "unknown"),
        "summary": pcb.get("summary", {}),
    }
    reconciliation_summary = {
        "id": "reconciliation:summary",
        "type": "reconciliation",
        **consistency.get("summary", {}),
    }
    evidence = [
        board_summary,
        *components,
        *pin_mappings,
        *(item for entries in facts.values() for item in entries),
        *deterministic_findings,
        reconciliation_summary,
        *reconciliation_findings,
    ]
    return {
        "review_input_version": "0.5",
        "evidence": evidence,
        "context": {
            "board_summary": board_summary["id"],
            "components": [item["id"] for item in components],
            "pin_mappings": [item["id"] for item in pin_mappings],
            "nets": [
                {"name": net["name"], "pin_mapping_ids": net["pin_mapping_ids"]}
                for net in schematic_nets
            ],
            "usb_engineering_facts": {
                "classification_policy": usb_facts.get("classification_policy", ""),
                "facts": {group: [item["id"] for item in entries]
                          for group, entries in facts.items()},
                "limitations": usb_facts.get("limitations", []),
            },
            "deterministic_checks": {
                "finding_counts": deterministic.get("finding_counts", {}),
                "finding_ids": [item["id"] for item in deterministic_findings],
                "limitations": deterministic.get("limitations", []),
            },
            "reconciliation": {
                "summary_id": reconciliation_summary["id"],
                "finding_ids": [item["id"] for item in reconciliation_findings],
            },
            "evidence_policy": unified_model.get("evidence_policy", {}),
            "source_kinds": {
                "schematic": "kicad-cli-netlist",
                "pcb": "kicad-ipc",
            },
        },
    }
