"""Regression tests for evidence-backed USB facts."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from ai_kicad.engineering_facts import extract_usb_facts
from ai_kicad.model import classify_net


def component(reference: str, value: str, *pads: tuple[str, str]) -> dict:
    return {
        "reference": reference,
        "value": value,
        "footprint": "Test:Footprint",
        "pads": [
            {"number": number, "net": net, "net_type": classify_net(net)}
            for number, net in pads
        ],
    }


def model(*components: dict, extra_nets: tuple[str, ...] = ()) -> dict:
    names = {
        pad["net"]
        for item in components
        for pad in item["pads"]
        if pad["net"]
    }
    names.update(extra_nets)
    return {
        "source": {"board": "fixture.kicad_pcb"},
        "components": list(components),
        "nets": [
            {"name": name, "net_type": classify_net(name)}
            for name in sorted(names)
        ],
    }


class ClassifyNetTests(unittest.TestCase):
    def test_classifies_missing_unconnected_and_named_nets(self) -> None:
        self.assertEqual(classify_net(""), "missing")
        self.assertEqual(classify_net("unconnected-(J1-1)"), "unconnected")
        self.assertEqual(classify_net("USB_SS_TX+"), "named")


class UsbFactsTests(unittest.TestCase):
    def test_extracts_connector_signal_pair_and_resistor_pad_evidence(self) -> None:
        source = model(
            component("J1", "USB-C", ("A4", "VBUS"), ("A5", "CC1"), ("B5", "CC2"),
                      ("A6", "D+"), ("A7", "D-"), ("A2", "USB_SS_TX+"),
                      ("A3", "USB_SS_TX-")),
            component("R3", "900k", ("1", "VB Det"), ("2", "VBUS")),
            component("U1", "PHY", ("1", "USB_SS_TX+"), ("2", "USB_SS_TX-")),
            extra_nets=("GND",),
        )

        usb = extract_usb_facts(source)["usb"]
        self.assertEqual([entry["reference"] for entry in usb["connector_candidates"]], ["J1"])
        self.assertEqual([entry["name"] for entry in usb["vbus_like_nets"]], ["VBUS"])
        self.assertEqual([entry["name"] for entry in usb["cc_nets"]], ["CC1", "CC2"])
        self.assertEqual([entry["name"] for entry in usb["named_usb2_d_plus_nets"]], ["D+"])
        self.assertEqual([entry["name"] for entry in usb["named_usb2_d_minus_nets"]], ["D-"])

        self.assertEqual(len(usb["superspeed_differential_pairs"]), 1)
        pair = usb["superspeed_differential_pairs"][0]
        self.assertEqual(pair["label"], "USB_SS_TX")
        self.assertEqual(pair["positive_net"], "USB_SS_TX+")
        self.assertEqual(pair["negative_net"], "USB_SS_TX-")
        self.assertEqual(pair["positive_endpoints"], [
            {"component": "J1", "pad": "A2"},
            {"component": "U1", "pad": "1"},
        ])
        self.assertIn("matching positive/negative net names", pair["evidence"])

        resistor = next(item for item in usb["resistor_components"] if item["reference"] == "R3")
        self.assertEqual(resistor["pads"], [
            {"number": "1", "net": "VB Det", "net_type": "named"},
            {"number": "2", "net": "VBUS", "net_type": "named"},
        ])
        self.assertEqual(resistor["nets"], ["VB Det", "VBUS"])

    def test_unconnected_names_never_become_usb_signal_candidates(self) -> None:
        generated = (
            "unconnected-(J1-VBUS-Pad1)",
            "unconnected-(J1-CC1-Pad2)",
            "unconnected-(J1-D+-Pad3)",
            "unconnected-(J1-USB_SS_TX+-Pad4)",
        )
        facts = extract_usb_facts(model(
            component("J1", "USB-C", *( (str(index), net) for index, net in enumerate(generated, 1) ))
        ))
        usb = facts["usb"]

        self.assertTrue(all(not usb[key] for key in (
            "vbus_like_nets", "cc_nets", "named_usb2_d_plus_nets",
            "named_usb2_d_minus_nets", "superspeed_differential_pairs",
        )))
        self.assertTrue(all(pad["net_type"] == "unconnected"
                            for pad in usb["connector_candidates"][0]["pads"]))

    def test_saved_adapter_artifact_has_expected_v03_shape_and_evidence(self) -> None:
        artifact = Path(__file__).resolve().parents[1] / "usb-facts.json"
        facts = json.loads(artifact.read_text(encoding="utf-8"))
        self.assertEqual(facts["facts_version"], "0.3")
        usb = facts["usb"]
        self.assertEqual(
            set(usb),
            {
                "connector_candidates", "vbus_like_nets", "ground_nets", "cc_nets",
                "named_usb2_d_plus_nets", "named_usb2_d_minus_nets",
                "superspeed_differential_pairs", "esd_protection_candidates",
                "resistor_components", "vbus_connected_components",
            },
        )
        j2 = next(item for item in usb["connector_candidates"] if item["reference"] == "J2")
        self.assertEqual(
            {(pad["number"], pad["net"]) for pad in j2["pads"] if pad["number"] in {"A5", "B5"}},
            {("A5", "CC conn"), ("B5", "CC conn")},
        )
        r3 = next(item for item in usb["resistor_components"] if item["reference"] == "R3")
        self.assertEqual(
            [(pad["number"], pad["net"]) for pad in r3["pads"] if pad["number"]],
            [("1", "VB Det"), ("2", "Net-(J2-VBUS_A4)")],
        )
        self.assertEqual(len(usb["superspeed_differential_pairs"]), 6)
        self.assertEqual(usb["named_usb2_d_plus_nets"], [])
        self.assertEqual(usb["named_usb2_d_minus_nets"], [])


if __name__ == "__main__":
    unittest.main()
