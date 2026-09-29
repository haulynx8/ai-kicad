"""Combine a saved PCB model with KiCad's schematic netlist export."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


class SchematicExportError(RuntimeError):
    """Raised when KiCad cannot export a schematic netlist."""


def _find_kicad_cli(explicit: str | Path | None = None) -> str:
    if explicit:
        return str(Path(explicit).expanduser())
    configured = os.environ.get("KICAD_CLI")
    if configured:
        return configured
    on_path = shutil.which("kicad-cli")
    if on_path:
        return on_path
    raise SchematicExportError(
        "kicad-cli was not found; install KiCad's CLI, add it to PATH, "
        "set KICAD_CLI, or pass --kicad-cli"
    )


def export_schematic_netlist(schematic_path: Path, kicad_cli: str | Path | None = None) -> ET.Element:
    """Export the schematic through KiCad and return the parsed XML root."""
    schematic_path = schematic_path.expanduser().resolve()
    if not schematic_path.is_file():
        raise SchematicExportError(f"schematic file does not exist: {schematic_path}")
    cli = _find_kicad_cli(kicad_cli)
    with tempfile.TemporaryDirectory(prefix="ai-kicad-netlist-") as temporary:
        output_path = Path(temporary) / "schematic-netlist.xml"
        command = [
            cli, "sch", "export", "netlist", "--format", "kicadxml",
            "--output", str(output_path), str(schematic_path),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
        except OSError as exc:
            raise SchematicExportError(f"could not run {cli}: {exc}") from exc
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
            raise SchematicExportError(f"KiCad schematic netlist export failed: {detail}")
        try:
            return ET.parse(output_path).getroot()
        except (OSError, ET.ParseError) as exc:
            raise SchematicExportError(f"KiCad did not produce a valid XML netlist: {exc}") from exc


def _text(element: ET.Element | None) -> str:
    return element.text.strip() if element is not None and element.text else ""


def _schematic_data(root: ET.Element) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    components: dict[str, dict[str, Any]] = {}
    component_root = root.find("components")
    for comp in component_root.findall("comp") if component_root is not None else []:
        ref = comp.get("ref", "")
        if not ref:
            continue
        libsource = comp.find("libsource")
        components[ref] = {
            "reference": ref,
            "value": _text(comp.find("value")),
            "footprint": _text(comp.find("footprint")),
            "library_id": ":".join(filter(None, (
                libsource.get("lib", "") if libsource is not None else "",
                libsource.get("part", "") if libsource is not None else "",
            ))),
            "pins": [],
        }

    nets: list[dict[str, Any]] = []
    pin_to_net: dict[tuple[str, str], dict[str, str]] = {}
    nets_root = root.find("nets")
    for net in nets_root.findall("net") if nets_root is not None else []:
        name = net.get("name", "")
        pins = []
        for node in net.findall("node"):
            pin = {
                "reference": node.get("ref", ""),
                "number": node.get("pin", ""),
                "function": node.get("pinfunction", ""),
                "electrical_type": node.get("pintype", ""),
            }
            pins.append(pin)
            pin_to_net[(pin["reference"], pin["number"])] = {
                "net": name,
                "net_type": "unconnected" if name.casefold().startswith("unconnected-(") else "named",
                "function": pin["function"],
                "electrical_type": pin["electrical_type"],
            }
        nets.append({
            "code": net.get("code", ""),
            "name": name,
            "net_type": "unconnected" if name.casefold().startswith("unconnected-(") else "named",
            "pins": sorted(pins, key=lambda item: (item["reference"], item["number"])),
        })

    for (ref, number), assignment in pin_to_net.items():
        component = components.get(ref)
        if component is not None:
            component["pins"].append({"number": number, **assignment})
    for component in components.values():
        component["pins"].sort(key=lambda item: item["number"])

    design = root.find("design")
    meta = {
        "tool": _text(design.find("tool")) if design is not None else "",
        "source": _text(design.find("source")) if design is not None else "",
    }
    return components, nets, {"pin_to_net": pin_to_net, **meta}


def build_unified_model(pcb_model: dict[str, Any], schematic_root: ET.Element) -> dict[str, Any]:
    """Join schematic symbol pins to PCB pads by reference and pad number."""
    sch_components, sch_nets, schematic_data = _schematic_data(schematic_root)
    pcb_components = {
        component.get("reference", ""): component
        for component in pcb_model.get("components", [])
        if component.get("reference")
    }
    pcb_pad_map: dict[tuple[str, str], dict[str, str]] = {}
    for ref, component in pcb_components.items():
        for pad in component.get("pads", []):
            number = str(pad.get("number", ""))
            if number:
                pcb_pad_map[(ref, number)] = {
                    "net": str(pad.get("net", "")),
                    "net_type": str(pad.get("net_type", "")),
                }

    pin_mappings = []
    pcb_source = pcb_model.get("source", {}).get("kind", "kicad-pcb-model")
    for (ref, number), schematic_pin in sorted(schematic_data["pin_to_net"].items()):
        pcb_pad = pcb_pad_map.get((ref, number))
        schematic_net = schematic_pin["net"]
        pcb_net = pcb_pad["net"] if pcb_pad is not None else None
        pcb_net_type = pcb_pad["net_type"] if pcb_pad is not None else None
        if pcb_pad is None:
            status = "schematic_only"
            comparison = "pcb_pad_not_found"
        elif schematic_pin["net_type"] == pcb_net_type == "unconnected":
            # KiCad encodes certain characters differently in generated
            # no-connect names across schematic and PCB file formats.
            status = "both_unconnected"
            comparison = "both_unconnected_by_classification"
        elif pcb_net == schematic_net:
            status = "matched"
            comparison = "exact_net_name_match"
        else:
            status = "net_mismatch"
            comparison = "net_name_mismatch"
        pin_mappings.append({
            "reference": ref,
            "pin": number,
            "function": schematic_pin["function"],
            "electrical_type": schematic_pin["electrical_type"],
            "schematic_net": schematic_net,
            "schematic_net_type": schematic_pin["net_type"],
            "pcb_net": pcb_net,
            "pcb_net_type": pcb_net_type,
            "status": status,
            "evidence_level": "derived",
            "provenance": {
                "schematic": {
                    "source": "kicad-cli-netlist",
                    "reference": ref,
                    "pin": number,
                },
                "pcb": {
                    "source": pcb_source,
                    "reference": ref,
                    "pad": number,
                    "found": pcb_pad is not None,
                },
                "comparison": comparison,
            },
        })

    schematic_pin_keys = set(schematic_data["pin_to_net"])
    pcb_only_pads = [
        {
            "reference": ref,
            "pad": number,
            **pad,
            "evidence_level": "derived",
            "provenance": {
                "schematic": {
                    "source": "kicad-cli-netlist",
                    "reference": ref,
                    "pin": number,
                    "found": False,
                },
                "pcb": {
                    "source": pcb_source,
                    "reference": ref,
                    "pad": number,
                    "found": True,
                },
                "comparison": "schematic_pin_not_found",
            },
        }
        for (ref, number), pad in sorted(pcb_pad_map.items())
        if (ref, number) not in schematic_pin_keys
    ]
    net_mismatches = [item for item in pin_mappings if item["status"] == "net_mismatch"]
    schematic_only_pins = [item for item in pin_mappings if item["status"] == "schematic_only"]

    components = []
    for ref in sorted(set(sch_components) | set(pcb_components)):
        sch = sch_components.get(ref)
        pcb = pcb_components.get(ref)
        components.append({
            "reference": ref,
            "schematic": ({key: value for key, value in sch.items() if key != "pins"}
                          if sch else None),
            "pcb": ({key: pcb.get(key) for key in ("value", "footprint", "side", "position_mm")}
                    if pcb else None),
            "evidence_level": "raw",
        })

    schematic_references = set(sch_components)
    pcb_references = set(pcb_components)
    schematic_only_components = [
        {
            "reference": ref,
            "evidence_level": "derived",
            "provenance": {
                "schematic": {"source": "kicad-cli-netlist", "reference": ref, "found": True},
                "pcb": {"source": pcb_source, "reference": ref, "found": False},
                "comparison": "pcb_component_not_found",
            },
        }
        for ref in sorted(schematic_references - pcb_references)
    ]
    pcb_only_components = [
        {
            "reference": ref,
            "evidence_level": "derived",
            "provenance": {
                "schematic": {"source": "kicad-cli-netlist", "reference": ref, "found": False},
                "pcb": {"source": pcb_source, "reference": ref, "found": True},
                "comparison": "schematic_component_not_found",
            },
        }
        for ref in sorted(pcb_references - schematic_references)
    ]
    component_value_mismatches = [
        {
            "reference": ref,
            "schematic_value": sch_components[ref]["value"],
            "pcb_value": pcb_components[ref].get("value", ""),
            "evidence_level": "derived",
            "provenance": {
                "schematic": {"source": "kicad-cli-netlist", "reference": ref, "field": "value"},
                "pcb": {"source": pcb_source, "reference": ref, "field": "value"},
                "comparison": "component_value_mismatch",
            },
        }
        for ref in sorted(schematic_references & pcb_references)
        if sch_components[ref]["value"] != pcb_components[ref].get("value", "")
    ]
    return {
        "schema_version": "0.4",
        "source": {
            "pcb": pcb_model.get("source", {}),
            "schematic": {
                "kind": "kicad-cli-netlist",
                "source": schematic_data["source"],
                "tool": schematic_data["tool"],
            },
        },
        "pcb": pcb_model,
        "components": components,
        "schematic_nets": sch_nets,
        "pin_mappings": pin_mappings,
        "consistency": {
            "summary": {
                "matched_pins": sum(item["status"] == "matched" for item in pin_mappings),
                "both_unconnected_pins": sum(item["status"] == "both_unconnected" for item in pin_mappings),
                "net_mismatches": len(net_mismatches),
                "schematic_only_pins": len(schematic_only_pins),
                "pcb_only_pads": len(pcb_only_pads),
                "schematic_only_components": len(schematic_only_components),
                "pcb_only_components": len(pcb_only_components),
                "component_value_mismatches": len(component_value_mismatches),
            },
            "net_mismatches": net_mismatches,
            "schematic_only_pins": schematic_only_pins,
            "pcb_only_pads": pcb_only_pads,
            "schematic_only_components": schematic_only_components,
            "pcb_only_components": pcb_only_components,
            "component_value_mismatches": component_value_mismatches,
        },
        "evidence_policy": {
            "raw": "KiCad schematic or PCB fields copied without interpretation.",
            "derived": "Pin and pad matches computed by component reference and pin/pad number; net agreement is exact-name comparison.",
            "inferred": "No engineering inferences are produced by this model.",
        },
    }


def render_unified_report(model: dict[str, Any]) -> str:
    consistency = model.get("consistency", {})
    summary = consistency.get("summary", {})
    lines = [
        "AI KiCad Inspector — Unified PCB + Schematic Model",
        "=" * 53,
        f"PCB: {model.get('source', {}).get('pcb', {}).get('board', 'unknown')}",
        f"Schematic: {model.get('source', {}).get('schematic', {}).get('source', 'unknown')}",
        "",
        "Pin and pad reconciliation",
        f"  matched pins: {summary.get('matched_pins', 0)}",
        f"  unconnected on both sides: {summary.get('both_unconnected_pins', 0)}",
        f"  net mismatches: {summary.get('net_mismatches', 0)}",
        f"  schematic-only pins: {summary.get('schematic_only_pins', 0)}",
        f"  PCB-only pads: {summary.get('pcb_only_pads', 0)}",
        "",
        "Schematic pin functions",
    ]
    usb_words = ("cc", "d+", "d-", "rx", "tx", "ss", "vbus")
    relevant = [
        pin for pin in model.get("pin_mappings", [])
        if any(word in pin.get("function", "").casefold() for word in usb_words)
    ]
    for pin in relevant:
        lines.append(
            f"  {pin['reference']}:{pin['pin']} {pin['function']} → "
            f"{pin['schematic_net']} [{pin['status']}]"
        )
    if not relevant:
        lines.append("  none identified from schematic pin functions")
    if consistency.get("net_mismatches"):
        lines.extend(["", "Net mismatches"])
        for pin in consistency["net_mismatches"]:
            lines.append(
                f"  {pin['reference']}:{pin['pin']} {pin['function']}: "
                f"schematic={pin['schematic_net']!r}, PCB={pin['pcb_net']!r}"
            )
    return "\n".join(lines) + "\n"
