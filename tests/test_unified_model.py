"""V0.4 reconciliation and provenance contract tests."""

from __future__ import annotations

import json
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from ai_kicad.unified_model import build_unified_model


def schematic_fixture() -> ET.Element:
    return ET.fromstring("""\
    <export version="E">
      <design><source>fixture.kicad_sch</source><tool>Eeschema 10.0.6</tool></design>
      <components>
        <comp ref="J1"><value>USB-C</value><footprint>Connector:USB_C</footprint>
          <libsource lib="Connector" part="USB_C"/></comp>
        <comp ref="R1"><value>10k</value><footprint>Resistor_SMD:R_0603</footprint>
          <libsource lib="Device" part="R"/></comp>
        <comp ref="J2"><value>Header</value><footprint>Connector:Header</footprint>
          <libsource lib="Connector" part="Header"/></comp>
      </components>
      <nets>
        <net code="1" name="CC conn"><node ref="J1" pin="A5" pinfunction="CC1_A5" pintype="bidirectional"/></net>
        <net code="2" name="SCHEMATIC_NET"><node ref="J1" pin="A6" pinfunction="D1+_A6" pintype="bidirectional"/></net>
        <net code="3" name="unconnected-(J1-NC-PadNC)"><node ref="J1" pin="NC" pinfunction="NC" pintype="passive"/></net>
        <net code="4" name="VBUS"><node ref="R1" pin="1" pintype="passive"/></net>
        <net code="5" name="SIGNAL"><node ref="R1" pin="2" pintype="passive"/></net>
        <net code="6" name="OTHER"><node ref="J2" pin="1" pinfunction="VBUS_A4" pintype="power_in"/></net>
      </nets>
    </export>
    """)


def pcb_fixture() -> dict:
    return {
        "schema_version": "0.2",
        "source": {"kind": "kicad-ipc", "board": "fixture.kicad_pcb"},
        "components": [
            {"reference": "J1", "value": "USB-C", "footprint": "Connector:USB_C", "pads": [
                {"number": "A5", "net": "CC conn", "net_type": "named"},
                {"number": "A6", "net": "PCB_NET", "net_type": "named"},
                {"number": "NC", "net": "unconnected-(J1-{slash}NC-PadNC)", "net_type": "unconnected"},
                {"number": "EXTRA", "net": "EXTRA_NET", "net_type": "named"},
            ]},
            {"reference": "R1", "value": "9k", "footprint": "Resistor_SMD:R_0603", "pads": [
                {"number": "1", "net": "VBUS", "net_type": "named"},
            ]},
            {"reference": "Q1", "value": "N-MOS", "footprint": "Package_TO_SOT_SMD:SOT-23", "pads": [
                {"number": "1", "net": "GND", "net_type": "named"},
            ]},
        ],
        "nets": [],
        "geometry": {"tracks": [], "vias": []},
    }


class UnifiedModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model = build_unified_model(pcb_fixture(), schematic_fixture())
        self.by_pin = {
            (pin["reference"], pin["pin"]): pin
            for pin in self.model["pin_mappings"]
        }

    def test_reconciliation_statuses_and_provenance(self) -> None:
        self.assertEqual(self.by_pin[("J1", "A5")]["status"], "matched")
        self.assertEqual(self.by_pin[("J1", "A6")]["status"], "net_mismatch")
        self.assertEqual(self.by_pin[("J1", "NC")]["status"], "both_unconnected")
        self.assertEqual(self.by_pin[("R1", "2")]["status"], "schematic_only")
        self.assertEqual(self.by_pin[("J2", "1")]["status"], "schematic_only")

        matched = self.by_pin[("J1", "A5")]
        self.assertEqual(matched["evidence_level"], "derived")
        self.assertEqual(matched["provenance"], {
            "schematic": {"source": "kicad-cli-netlist", "reference": "J1", "pin": "A5"},
            "pcb": {"source": "kicad-ipc", "reference": "J1", "pad": "A5", "found": True},
            "comparison": "exact_net_name_match",
        })
        self.assertEqual(self.by_pin[("J1", "A6")]["provenance"]["comparison"], "net_name_mismatch")
        self.assertEqual(self.by_pin[("J1", "NC")]["provenance"]["comparison"], "both_unconnected_by_classification")
        self.assertEqual(self.by_pin[("R1", "2")]["provenance"]["pcb"]["found"], False)

    def test_extra_pads_and_components_have_provenance(self) -> None:
        consistency = self.model["consistency"]
        self.assertEqual(consistency["summary"], {
            "matched_pins": 2,
            "both_unconnected_pins": 1,
            "net_mismatches": 1,
            "schematic_only_pins": 2,
            "pcb_only_pads": 2,
            "schematic_only_components": 1,
            "pcb_only_components": 1,
            "component_value_mismatches": 1,
        })
        extra = next(item for item in consistency["pcb_only_pads"] if item["pad"] == "EXTRA")
        self.assertEqual(extra["evidence_level"], "derived")
        self.assertEqual(extra["provenance"]["comparison"], "schematic_pin_not_found")
        self.assertTrue(consistency["schematic_only_components"][0]["provenance"])
        self.assertTrue(consistency["pcb_only_components"][0]["provenance"])
        self.assertTrue(consistency["component_value_mismatches"][0]["provenance"])

    def test_evidence_levels_do_not_claim_inference(self) -> None:
        self.assertEqual(self.model["components"][0]["evidence_level"], "raw")
        self.assertIn("No engineering inferences", self.model["evidence_policy"]["inferred"])

    def test_saved_adapter_v04_artifact_contract(self) -> None:
        artifact = Path(__file__).resolve().parents[1] / "a-c_adapter-unified.json"
        unified = json.loads(artifact.read_text(encoding="utf-8"))
        self.assertEqual(unified["schema_version"], "0.4")
        self.assertIn("geometry", unified["pcb"])
        summary = unified["consistency"]["summary"]
        self.assertEqual(summary["matched_pins"], 43)
        self.assertEqual(summary["both_unconnected_pins"], 41)
        self.assertEqual(summary["net_mismatches"], 0)
        self.assertEqual(summary["schematic_only_pins"], 0)
        self.assertEqual(summary["pcb_only_pads"], 0)

        j2_cc1 = next(item for item in unified["pin_mappings"]
                      if item["reference"] == "J2" and item["pin"] == "A5")
        self.assertEqual(j2_cc1["function"], "CC1_A5")
        self.assertEqual(j2_cc1["provenance"]["comparison"], "exact_net_name_match")
        self.assertEqual(j2_cc1["provenance"]["schematic"]["source"], "kicad-cli-netlist")
        self.assertEqual(j2_cc1["provenance"]["pcb"]["source"], "kicad-ipc")


if __name__ == "__main__":
    unittest.main()
