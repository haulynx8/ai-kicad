"""Stable, JSON-serializable V0.1 design model."""

from __future__ import annotations

from typing import Any


def _field_value(field: Any) -> str:
    try:
        return str(field.text.value)
    except (AttributeError, TypeError):
        return ""


def _mm(nanometres: int | float | None) -> float | None:
    return round(nanometres / 1_000_000, 6) if nanometres is not None else None


def classify_net(name: str | None) -> str:
    """Classify only what the KiCad net name establishes directly."""
    value = str(name or "").strip()
    if not value:
        return "missing"
    if value.casefold().startswith("unconnected-("):
        return "unconnected"
    return "named"


def board_to_model(board: Any) -> dict[str, Any]:
    """Copy the open board into our own primitive-only model.

    No KiCad object escapes this function. Geometry is in millimetres and
    original integer nanometres are retained for exact measurements.
    """
    footprints = board.get_footprints()
    nets = board.get_nets()
    tracks = board.get_tracks()
    vias = board.get_vias()
    zones = board.get_zones()
    layers = board.get_enabled_layers()

    components = []
    for footprint in footprints:
        pads = []
        for pad in footprint.definition.pads:
            # IPC can expose footprint-generated helper apertures as pads.
            # Blank-numbered objects are not electrical terminals, even when
            # their padstack carries a default copper-layer entry.
            if not str(pad.number).strip() or not pad.padstack.copper_layers:
                continue
            net = pad.net
            net_name = str(net.name) if net else ""
            pads.append({
                "number": str(pad.number),
                # KiCad 10.0.6's IPC Pad exposes a number and net, but no
                # electrical pin-name property. Keep the model key stable.
                "name": str(getattr(pad, "name", "")),
                "net": net_name,
                "net_type": classify_net(net_name),
            })
        position = footprint.position
        components.append({
            "reference": _field_value(footprint.reference_field),
            "value": _field_value(footprint.value_field),
            "footprint": str(footprint.definition.id),
            "side": str(board.get_layer_name(footprint.layer)),
            "position_mm": {"x": _mm(position.x), "y": _mm(position.y)},
            "pads": pads,
        })

    net_models = [
        {"name": str(net.name), "net_type": classify_net(str(net.name))}
        for net in nets if net.name
    ]
    layer_models = [{"id": int(layer), "name": str(board.get_layer_name(layer))} for layer in layers]

    return {
        "schema_version": "0.2",
        "source": {"kind": "kicad-ipc", "board": str(board.name)},
        "summary": {
            "components": len(components),
            "nets": len(net_models),
            "layers": int(board.get_copper_layer_count()),
            "enabled_layers": len(layer_models),
            "tracks": len(tracks),
            "vias": len(vias),
            "zones": len(zones),
        },
        "components": sorted(components, key=lambda item: item["reference"]),
        "nets": sorted(net_models, key=lambda item: item["name"]),
        "layers": layer_models,
        "geometry": {
            "tracks": [
                {
                    "net": str(track.net.name) if track.net else "",
                    "net_type": classify_net(str(track.net.name) if track.net else ""),
                    "layer": str(board.get_layer_name(track.layer)),
                    "width_nm": int(track.width),
                    "length_nm": round(track.length()),
                    "start_nm": {"x": int(track.start.x), "y": int(track.start.y)},
                    "end_nm": {"x": int(track.end.x), "y": int(track.end.y)},
                }
                for track in tracks
            ],
            "vias": [
                {
                    "net": str(via.net.name) if via.net else "",
                    "net_type": classify_net(str(via.net.name) if via.net else ""),
                    "position_nm": {"x": int(via.position.x), "y": int(via.position.y)},
                    "diameter_nm": int(via.diameter),
                    "drill_nm": int(via.drill_diameter),
                }
                for via in vias
            ],
        },
    }
