"""Deterministic structural checks over the V0.1 JSON model."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ai_kicad.model import classify_net


def analyze_model(model: dict[str, Any]) -> dict[str, Any]:
    """Return stable findings based only on facts present in the JSON model."""
    components = model.get("components", [])
    nets = model.get("nets", [])
    geometry = model.get("geometry", {})
    tracks = geometry.get("tracks", [])
    vias = geometry.get("vias", [])
    summary = model.get("summary", {})
    findings: list[dict[str, Any]] = []

    def add(rule: str, severity: str, title: str, detail: str, count: int | None = None) -> None:
        finding: dict[str, Any] = {
            "rule": rule,
            "severity": severity,
            "title": title,
            "detail": detail,
        }
        if count is not None:
            finding["count"] = count
        findings.append(finding)

    if not components:
        add(
            "board.no_components",
            "WARN",
            "No placed components",
            "The board has no footprints, so component and pad connectivity checks are limited.",
        )

    references = [str(component.get("reference", "")).strip() for component in components]
    blank_references = sum(not reference for reference in references)
    if blank_references:
        add(
            "component.blank_reference",
            "ERROR",
            "Component reference is empty",
            "Each placed footprint should have a non-empty reference.",
            blank_references,
        )

    duplicate_references = sorted(
        reference for reference, count in Counter(ref for ref in references if ref).items() if count > 1
    )
    if duplicate_references:
        add(
            "component.duplicate_reference",
            "ERROR",
            "Duplicate component references",
            "Repeated references: " + ", ".join(duplicate_references),
            len(duplicate_references),
        )

    missing_footprints = sum(not str(component.get("footprint", "")).strip() for component in components)
    if missing_footprints:
        add(
            "component.missing_footprint_id",
            "ERROR",
            "Footprint identifier is missing",
            "One or more placed components have no footprint library identifier in the model.",
            missing_footprints,
        )

    net_names = {str(net.get("name", "")) for net in nets if str(net.get("name", ""))}
    assigned_pad_count = 0
    missing_pad_count = 0
    unconnected_pad_count = 0
    unknown_pad_nets: set[str] = set()
    for component in components:
        for pad in component.get("pads", []):
            net_name = str(pad.get("net", "")).strip()
            net_type = str(pad.get("net_type") or classify_net(net_name))
            if net_type == "missing":
                missing_pad_count += 1
            elif net_type == "unconnected":
                assigned_pad_count += 1
                unconnected_pad_count += 1
                if net_name not in net_names:
                    unknown_pad_nets.add(net_name)
            else:
                assigned_pad_count += 1
                if net_name not in net_names:
                    unknown_pad_nets.add(net_name)

    if missing_pad_count:
        add(
            "pad.missing_net",
            "ERROR",
            "Pad has no net assignment",
            "One or more pads have no net name in the extracted model.",
            missing_pad_count,
        )

    if unconnected_pad_count:
        add(
            "pad.kicad_unconnected_net",
            "INFO",
            "Pads use KiCad unconnected nets",
            "KiCad marks these pads with generated unconnected-(...) nets; review them as no-connects.",
            unconnected_pad_count,
        )

    if unknown_pad_nets:
        names = ", ".join(sorted(unknown_pad_nets))
        add(
            "pad.net_missing_from_catalog",
            "ERROR",
            "Pad references an unknown net",
            "These pad net names are absent from the board net catalog: " + names,
            len(unknown_pad_nets),
        )

    unknown_geometry_nets: set[str] = set()
    unassigned_tracks = 0
    for track in tracks:
        net_name = str(track.get("net", "")).strip()
        net_type = str(track.get("net_type") or classify_net(net_name))
        if net_type == "missing":
            unassigned_tracks += 1
        elif net_name not in net_names:
            unknown_geometry_nets.add(net_name)

    unassigned_vias = 0
    for via in vias:
        net_name = str(via.get("net", "")).strip()
        net_type = str(via.get("net_type") or classify_net(net_name))
        if net_type == "missing":
            unassigned_vias += 1
        elif net_name not in net_names:
            unknown_geometry_nets.add(net_name)

    if unknown_geometry_nets:
        add(
            "geometry.net_missing_from_catalog",
            "ERROR",
            "Copper geometry references an unknown net",
            "These track/via net names are absent from the board net catalog: "
            + ", ".join(sorted(unknown_geometry_nets)),
            len(unknown_geometry_nets),
        )

    if unassigned_tracks or unassigned_vias:
        add(
            "geometry.unassigned_net",
            "WARN",
            "Copper geometry has no net assignment",
            f"{unassigned_tracks} track segments and {unassigned_vias} vias have no net name.",
            unassigned_tracks + unassigned_vias,
        )

    zero_length_tracks = sum(int(track.get("length_nm", 0)) <= 0 for track in tracks)
    if zero_length_tracks:
        add(
            "track.nonpositive_length",
            "WARN",
            "Track has zero or negative length",
            "One or more extracted track segments have a non-positive length.",
            zero_length_tracks,
        )

    model_counts = {
        "components": len(components),
        "nets": len(nets),
        "enabled_layers": len(model.get("layers", [])),
        "tracks": len(tracks),
        "vias": len(vias),
    }
    for key, actual_count in model_counts.items():
        recorded_count = summary.get(key)
        if recorded_count is not None and int(recorded_count) != actual_count:
            add(
                "model.summary_mismatch",
                "ERROR",
                "Summary count does not match model data",
                f"summary.{key} is {recorded_count}, while the extracted records contain {actual_count}.",
            )

    findings.sort(key=lambda item: (item["severity"], item["rule"]))
    counts = Counter(finding["severity"] for finding in findings)
    return {
        "report_version": "0.2.1",
        "source": model.get("source", {}),
        "board_summary": summary,
        "checks": {
            "components": len(components),
            "nets": len(nets),
            "assigned_pads": assigned_pad_count,
            "missing_net_pads": missing_pad_count,
            "unconnected_net_pads": unconnected_pad_count,
            "tracks": len(tracks),
            "vias": len(vias),
        },
        "finding_counts": {
            "ERROR": counts["ERROR"],
            "WARN": counts["WARN"],
            "INFO": counts["INFO"],
        },
        "findings": findings,
        "limitations": [
            "These checks validate model consistency only; they do not establish electrical correctness.",
            "The model has no schematic intent, component specifications, stackup impedance data, or DRC results.",
            "KiCad unconnected-(...) nets are treated as no-connect markers and reported as informational; confirm their intent against the schematic.",
        ],
    }


def render_report(report: dict[str, Any]) -> str:
    source = report.get("source", {})
    summary = report.get("board_summary", {})
    counts = report.get("finding_counts", {})
    lines = [
        "AI KiCad Inspector — Deterministic Checks",
        "=" * 42,
        f"Board: {source.get('board', 'unknown')}",
        "",
        "Model summary",
    ]
    labels = {
        "components": "Components",
        "nets": "Nets",
        "layers": "Copper layers",
        "enabled_layers": "Enabled layers",
        "tracks": "Tracks",
        "vias": "Vias",
        "zones": "Zones",
    }
    for key, label in labels.items():
        if key in summary:
            lines.append(f"  {label:16} {summary[key]}")
    lines.extend([
        "",
        f"Findings: {counts.get('ERROR', 0)} errors, {counts.get('WARN', 0)} warnings, "
        f"{counts.get('INFO', 0)} informational",
    ])
    for finding in report.get("findings", []):
        suffix = f" ({finding['count']})" if "count" in finding else ""
        lines.extend([
            "",
            f"[{finding['severity']}] {finding['title']}{suffix}",
            f"  {finding['detail']}",
        ])
    lines.extend(["", "Limits"])
    lines.extend(f"  - {limitation}" for limitation in report.get("limitations", []))
    return "\n".join(lines) + "\n"
