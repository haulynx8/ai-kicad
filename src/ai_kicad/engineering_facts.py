"""Evidence-backed USB facts extracted deterministically from the design model."""

from __future__ import annotations

import re
from typing import Any

from ai_kicad.model import classify_net


def _pad_endpoints(components: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = {}
    for component in components:
        for pad in component.get("pads", []):
            name = str(pad.get("net", "")).strip()
            if not name:
                continue
            result.setdefault(name, []).append({
                "component": str(component.get("reference", "")),
                "pad": str(pad.get("number", "")),
            })
    for endpoints in result.values():
        endpoints.sort(key=lambda endpoint: (endpoint["component"], endpoint["pad"]))
    return result


def _is_usb_data_net(name: str, positive: bool) -> bool:
    token = re.sub(r"[^A-Z0-9+\-]", "", name.upper())
    aliases = {"D+", "DPLUS", "DP", "USBDP", "USBDPLUS", "USB2DP"} if positive else {
        "D-", "DMINUS", "DM", "USBDM", "USBDMINUS", "USB2DM"
    }
    return token in aliases


def _pair_base(name: str) -> tuple[str, str] | None:
    trimmed = name.strip()
    for suffix in (" +", "+", " -", "-"):
        if trimmed.endswith(suffix):
            sign = "+" if "+" in suffix else "-"
            base = trimmed[:-len(suffix)].strip()
            return (base, sign) if base else None
    return None


def _is_superspeed_label(name: str) -> bool:
    # Treat common separators in KiCad net labels as word boundaries. Python's
    # ``\b`` considers underscore part of a word, so USB_SS_TX would otherwise
    # fail to match the standalone ``SS`` token.
    normalized = re.sub(r"[_-]+", " ", name)
    return bool(re.search(r"\b(?:ss|sspeed|usb\s*3)\b|super.?speed", normalized, re.IGNORECASE))


def extract_usb_facts(model: dict[str, Any]) -> dict[str, Any]:
    """Build USB-related facts with the exact source names/pads as evidence.

    Classification here is lexical and structural. It does not map connector
    pad numbers to USB pin functions when those functions are absent from the
    IPC model.
    """
    components = model.get("components", [])
    nets = model.get("nets", [])
    net_names = sorted({
        str(net.get("name", "")).strip()
        for net in nets
        if net.get("name") and (net.get("net_type") or classify_net(net.get("name"))) == "named"
    })
    endpoints = _pad_endpoints(components)

    connector_candidates = []
    for component in components:
        reference = str(component.get("reference", ""))
        if reference.upper().startswith("J"):
            connector_candidates.append({
                "reference": reference,
                "value": str(component.get("value", "")),
                "footprint": str(component.get("footprint", "")),
                "pads": [
                    {"number": str(pad.get("number", "")),
                     "net": str(pad.get("net", "")),
                     "net_type": str(pad.get("net_type") or classify_net(pad.get("net")))}
                    for pad in component.get("pads", [])
                ],
                "evidence": "reference begins with J",
            })

    vbus_nets = [name for name in net_names if "vbus" in name.casefold()]
    ground_nets = [
        name for name in net_names
        if re.fullmatch(r"(?:gnd|ground)(?:[_-][a-z0-9]+)?", name.strip(), re.IGNORECASE)
    ]
    cc_nets = [
        name for name in net_names
        if re.search(r"(?:^|[^a-z0-9])cc(?:[ _-]?[12])?(?:$|[^a-z0-9])", name, re.IGNORECASE)
    ]
    d_plus_nets = [name for name in net_names if _is_usb_data_net(name, positive=True)]
    d_minus_nets = [name for name in net_names if _is_usb_data_net(name, positive=False)]

    pairs_by_base: dict[str, tuple[str, str]] = {}
    for name in net_names:
        split = _pair_base(name)
        if not split or split[1] != "-":
            continue
        base, _ = split
        if not _is_superspeed_label(base):
            continue
        positive = next((candidate for candidate in (base, base + " +", base + "+") if candidate in net_names), "")
        if positive:
            pairs_by_base[base] = (positive, name)

    superspeed_pairs = []
    for base, (positive, negative) in sorted(pairs_by_base.items()):
        superspeed_pairs.append({
            "label": base,
            "positive_net": positive,
            "negative_net": negative,
            "positive_endpoints": endpoints.get(positive, []),
            "negative_endpoints": endpoints.get(negative, []),
            "evidence": "matching positive/negative net names containing an SS/SSpeed/USB3 label",
        })

    esd_devices = []
    resistors = []
    vbus_connected_components = []
    for component in components:
        reference = str(component.get("reference", ""))
        value = str(component.get("value", ""))
        footprint = str(component.get("footprint", ""))
        searchable = f"{value} {footprint}".casefold()
        component_nets = sorted({
            str(pad.get("net", "")).strip()
            for pad in component.get("pads", [])
            if pad.get("net") and (pad.get("net_type") or classify_net(pad.get("net"))) == "named"
        })
        pad_facts = [
            {"number": str(pad.get("number", "")), "net": str(pad.get("net", "")),
             "net_type": str(pad.get("net_type") or classify_net(pad.get("net")))}
            for pad in component.get("pads", [])
        ]
        if any(token in searchable for token in ("esd", "tvs", "transient")):
            esd_devices.append({
                "reference": reference,
                "value": value,
                "footprint": footprint,
                "nets": component_nets,
                "pads": pad_facts,
                "evidence": "value or footprint identifier contains ESD, TVS, or transient",
            })
        if reference.upper().startswith("R"):
            resistors.append({
                "reference": reference,
                "value": value,
                "nets": component_nets,
                "pads": pad_facts,
                "evidence": "reference begins with R",
            })
        connected_vbus_nets = sorted(set(component_nets) & set(vbus_nets))
        other_nets = sorted(set(component_nets) - set(connected_vbus_nets))
        if connected_vbus_nets:
            vbus_connected_components.append({
                "reference": reference,
                "value": value,
                "vbus_like_nets": connected_vbus_nets,
                "other_nets": other_nets,
                "evidence": "component pads share a net whose name contains VBUS",
            })

    return {
        "facts_version": "0.3",
        "source": model.get("source", {}),
        "classification_policy": "Names and reference conventions only; no unprovided pin-function semantics are inferred.",
        "usb": {
            "connector_candidates": connector_candidates,
            "vbus_like_nets": [{"name": name, "endpoints": endpoints.get(name, [])} for name in vbus_nets],
            "ground_nets": [{"name": name, "endpoints": endpoints.get(name, [])} for name in ground_nets],
            "cc_nets": [{"name": name, "endpoints": endpoints.get(name, [])} for name in cc_nets],
            "named_usb2_d_plus_nets": [{"name": name, "endpoints": endpoints.get(name, [])} for name in d_plus_nets],
            "named_usb2_d_minus_nets": [{"name": name, "endpoints": endpoints.get(name, [])} for name in d_minus_nets],
            "superspeed_differential_pairs": superspeed_pairs,
            "esd_protection_candidates": esd_devices,
            "resistor_components": resistors,
            "vbus_connected_components": vbus_connected_components,
        },
        "limitations": [
            "KiCad 10.0.6 IPC provides pad numbers and net assignments here, but not electrical pad names.",
            "USB connector roles and CC1/CC2 or D+/D- functions are not assigned from pad numbers without symbol pin semantics.",
            "ESD and resistor component roles are candidates based on value, footprint identifier, and reference prefix.",
        ],
    }


def render_usb_facts(facts: dict[str, Any]) -> str:
    source = facts.get("source", {})
    usb = facts.get("usb", {})
    lines = [
        "AI KiCad Inspector — USB Engineering Facts",
        "=" * 45,
        f"Board: {source.get('board', 'unknown')}",
        "",
        "Connector candidates",
    ]
    connectors = usb.get("connector_candidates", [])
    if not connectors:
        lines.append("  none identified by J-reference convention")
    for connector in connectors:
        lines.append(f"  {connector['reference']} {connector['value']} ({len(connector['pads'])} pads)")
        for pad in connector["pads"]:
            net = pad["net"] or "<missing>"
            lines.append(f"    pad {pad['number'] or '?'} → {net} [{pad['net_type']}]")

    groups = [
        ("VBUS-like nets", usb.get("vbus_like_nets", [])),
        ("Ground nets", usb.get("ground_nets", [])),
        ("CC-named nets", usb.get("cc_nets", [])),
        ("Explicitly named USB 2 D+ nets", usb.get("named_usb2_d_plus_nets", [])),
        ("Explicitly named USB 2 D- nets", usb.get("named_usb2_d_minus_nets", [])),
    ]
    for title, entries in groups:
        lines.extend(["", title])
        lines.extend(f"  {entry['name']} ← {', '.join(e['component'] + ':' + e['pad'] for e in entry['endpoints']) or 'no pad endpoints'}" for entry in entries)
        if not entries:
            lines.append("  none matched by net name")

    lines.extend(["", "SuperSpeed differential pairs"])
    pairs = usb.get("superspeed_differential_pairs", [])
    for pair in pairs:
        lines.append(f"  {pair['label']}: {pair['positive_net']} / {pair['negative_net']}")
    if not pairs:
        lines.append("  none matched by net names")

    lines.extend(["", "Protection candidates"])
    for device in usb.get("esd_protection_candidates", []):
        lines.append(f"  {device['reference']} {device['value']}: {', '.join(device['nets'])}")
    if not usb.get("esd_protection_candidates"):
        lines.append("  none matched by value/footprint text")

    lines.extend(["", "Resistor components"])
    for resistor in usb.get("resistor_components", []):
        numbered_pads = [pad for pad in resistor["pads"] if pad["number"]]
        lines.append(f"  {resistor['reference']} {resistor['value']}: "
                     f"{', '.join(pad['number'] + '=' + (pad['net'] or '<missing>') for pad in numbered_pads) or 'no numbered pads'}")
    if not usb.get("resistor_components"):
        lines.append("  none matched by R-reference convention")

    lines.extend(["", "VBUS-connected components"])
    for component in usb.get("vbus_connected_components", []):
        lines.append(f"  {component['reference']} {component['value']}: "
                     f"VBUS-like {', '.join(component['vbus_like_nets'])}; "
                     f"other {', '.join(component['other_nets']) or 'none'}")
    if not usb.get("vbus_connected_components"):
        lines.append("  none")

    lines.extend(["", "Limits"])
    lines.extend(f"  - {limit}" for limit in facts.get("limitations", []))
    return "\n".join(lines) + "\n"
