# AI KiCad Inspector — V0.5 foundations

Read-only extraction of the PCB open in KiCad into a versioned JSON design model. It uses the official `kicad-python` IPC bindings; it does not use the deprecated `pcbnew` SWIG API and does not modify the board.

## Machine checked for this first adapter

- KiCad PCB Editor: **10.0.6** (AppImage at `~/Applications/KiCad/kicad-10.0.6-x86_64.AppImage`)
- KiCad's bundled Python: **3.11**
- Bundled IPC plugin schema: `share/kicad/schemas/api.v1.schema.json`
- The AppImage includes legacy `pcbnew` bindings, but no `kipy` package. The project declares `kicad-python` 0.8.x, whose API supports KiCad 10.0.6.

KiCad 10's IPC API needs a running GUI and supports PCB Editor plugins. The plugin receives its socket and token from KiCad. The IPC path has been exercised against the USB-A to USB-C adapter project in `~/.local/share/kicad/10.0/projects/a-c_adapter`.

## Install for development

```bash
cd ~/Projects/ai-kicad
python -m venv .venv
. .venv/bin/activate
pip install -e .
```

Open a board in KiCad PCB Editor, then run:

```bash
ai-kicad inspect
ai-kicad inspect --output design-model.json
```

KiCad launches its plugin action with the IPC connection environment variables. Register the plugin from KiCad PCB Editor's IPC plugin settings using this directory's `plugin.json`. The command can also run from a shell that inherited `KICAD_API_SOCKET` and `KICAD_API_TOKEN` from KiCad.

## Design model 0.2

The JSON model contains board identity, summary counts, enabled layer IDs/names, footprints and pads with net assignments, named nets, and track/via geometry. `summary.layers` is the copper stack layer count; `summary.enabled_layers` counts all enabled copper and technical layers listed in `layers`. Geometry stores positions, widths and lengths as integer nanometres; footprint locations also have millimetre convenience values. Zones are counted but their polygons and fill details are not extracted yet.

Only pads with copper layers are included as electrical terminals; paste-only apertures are omitted. Every included pad, net, track, and via has a `net_type`: `named` for an ordinary non-empty net name, `unconnected` for KiCad's generated `unconnected-(...)` names, and `missing` where no net name is present. `schema_version` is `0.2`.

The extractor copies values into plain Python dictionaries before serialization. No KiCad API objects reach the AI-facing model. Electrical judgments, DRC access, AI providers, schematic parsing, and all modifications are outside this first extraction milestone.

## V0.2.1 deterministic checks

Run checks against a saved model without a KiCad connection:

```bash
ai-kicad analyze design-model.json
ai-kicad analyze design-model.json --output findings.json
```

Checks model consistency: missing/duplicate references, missing footprint IDs, pad and copper net names absent from the net catalog, truly missing pad nets (errors), KiCad unconnected pads (informational), non-positive track lengths, and summary counts that disagree with the extracted records. Findings are deterministic and include stable rule IDs. They do not claim electrical correctness or replace KiCad DRC.

## V0.3 engineering facts

Extract evidence-backed USB facts without an LLM:

```bash
ai-kicad facts usb-adapter.json
ai-kicad facts usb-adapter.json --output usb-facts.json
```

The facts include connector candidates identified by the J-reference convention, nets matched by explicit VBUS/GND/CC/D+/D- names, SuperSpeed differential pairs matched by positive/negative net labels, ESD protection candidates identified from value/footprint text, resistor components, and components sharing VBUS-like nets. Each fact keeps the supporting net names, component references, pad numbers, or matched convention. The extractor does not infer connector pad functions or claim electrical correctness where the model lacks that information.

The JSON root contains `facts_version`, `source`, `classification_policy`, `usb`, and `limitations`. `usb` contains `connector_candidates`, `vbus_like_nets`, `ground_nets`, `cc_nets`, `named_usb2_d_plus_nets`, `named_usb2_d_minus_nets`, `superspeed_differential_pairs`, `esd_protection_candidates`, `resistor_components`, and `vbus_connected_components`. Unconnected KiCad names remain visible as connector pad evidence with `net_type: "unconnected"`; they are excluded from the named USB signal candidate lists. SuperSpeed entries mean only that the net labels form a lexical positive/negative match.

Run the regression contracts with the standard library:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## V0.4 unified PCB + schematic model

KiCad 10 exposes PCB Editor data through IPC, while its IPC API does not support schematic editor plugins. V0.4 therefore asks KiCad's own `kicad-cli` to export a schematic XML netlist, then joins the exported symbol pins to PCB pads by component reference and pin/pad number. This preserves schematic pin functions and electrical types without guessing from net names.

```bash
ai-kicad unify usb-adapter.json \
  ~/.local/share/kicad/10.0/projects/a-c_adapter/a-c_adapter.kicad_sch \
  --kicad-cli ~/Applications/KiCad/AppDir/bin/kicad-cli \
  --output a-c_adapter-unified.json
```

`--kicad-cli` can be omitted when `kicad-cli` is on `PATH` or configured through `KICAD_CLI`. The unified model retains the complete PCB model under `pcb`, and adds a joined `components` view, schematic nets and their pin functions, `pin_mappings`, and `consistency` findings. Statuses distinguish exact named-net matches, net mismatches, schematic-only pins, PCB-only pads, and pins unconnected on both sides. Synthetic KiCad no-connect names are compared by their `unconnected` classification because special characters may be serialized differently in the schematic and PCB files.

Every derived pin mapping and consistency finding has `evidence_level` and `provenance`. Pin provenance identifies the source and component pin/pad on both sides, plus the comparison performed (for example, `exact_net_name_match` or `both_unconnected_by_classification`). A missing side is recorded with `found: false`. Raw KiCad fields remain in the source models; the unified model makes no engineering inferences.

For the adapter, the current reconciliation found 43 exact named-net pin matches, 41 pins unconnected on both sides, and no mismatches or missing/extra pads. It identifies J2 A5 as `CC1_A5`, B5 as `CC2_B5`, A6/A7 as D1+/D1−, and provides the TX/RX pin functions for the SuperSpeed lanes. These are raw KiCad pin semantics joined to PCB nets; the V0.4 model itself makes no electrical design inference.

## V0.5 AI engineering review

V0.5 sends a compact projection of the unified model, deterministic checks, and USB engineering facts to an Ollama chat model. It does not send KiCad objects, expose tools to the model, or modify the board or schematic.

```bash
ai-kicad review a-c_adapter-unified.json --model my-local-model
ai-kicad review a-c_adapter-unified.json --model my-local-model --output review.json
```

The Ollama server defaults to `http://localhost:11434`; override it with `--ollama-url` or `OLLAMA_HOST`. Set `OLLAMA_MODEL` to avoid repeating `--model`. Requests default to a 32,768-token context (`--context-length`) and a 900-second timeout (`--timeout`) for local CPU inference. Thinking is disabled and generated output is capped at 1,200 tokens to keep reviews concise. Ollama's structured output mode constrains its response to the review JSON Schema. The review input separates a top-level `evidence` registry from `context`; context groups contain references to registry IDs. The local validator accepts citations only from that registry, rejects duplicate IDs, checks cited metadata, and requires every finding to be `inferred`. Review findings contain severity, claim, evidence references, reasoning, confidence, and a suggested action. No finding is returned as a KiCad fact.

The provider is isolated behind `ReviewProvider`, so additional model services can implement the same contract. Tests for report validation and compact review input run without Ollama; `tests/test_ollama_provider.py` exercises the HTTP adapter with a mocked response. `tests/fixtures/adapter-review-v05.json` is a hand-authored representative response checked against the actual adapter evidence registry; it is not a live model result.
