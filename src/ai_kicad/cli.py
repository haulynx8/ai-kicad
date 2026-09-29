"""Command-line entry point. V0.1 reads only the PCB already open in KiCad."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ai_kicad.checks import analyze_model, render_report
from ai_kicad.engineering_facts import extract_usb_facts, render_usb_facts
from ai_kicad.model import board_to_model
from ai_kicad.review.ollama import OllamaProvider
from ai_kicad.review.provider import ReviewProviderError
from ai_kicad.unified_model import (
    SchematicExportError,
    build_unified_model,
    export_schematic_netlist,
    render_unified_report,
)
from ai_kicad.review_input import build_review_input


def inspect(output: Path | None = None) -> int:
    try:
        from kipy import KiCad
    except ImportError:
        print("KiCad IPC bindings are missing. Install this project with: pip install -e .", file=sys.stderr)
        return 2

    try:
        board = KiCad().get_board()
        model = board_to_model(board)
    except Exception as exc:  # IPC errors vary by KiCad/binding version.
        print(f"Could not read an open PCB through KiCad IPC: {exc}", file=sys.stderr)
        return 2

    serialized = json.dumps(model, indent=2, ensure_ascii=False) + "\n"
    if output:
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            temp_path = output.with_name(output.name + ".tmp")
            temp_path.write_text(serialized, encoding="utf-8")
            temp_path.replace(output)
        except OSError as exc:
            print(f"Could not write {output}: {exc}", file=sys.stderr)
            return 2
        print(f"Wrote {output}")
    else:
        print(serialized, end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai-kicad", description="Read-only KiCad PCB inspector")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect", help="extract the PCB currently open in KiCad as JSON")
    inspect_parser.add_argument("--output", "-o", type=Path, help="write JSON to a file instead of stdout")
    analyze_parser = subparsers.add_parser("analyze", help="run deterministic checks on a saved design model")
    analyze_parser.add_argument("model", type=Path, help="path to a design-model JSON file")
    analyze_parser.add_argument("--output", "-o", type=Path, help="write the structured report as JSON")
    facts_parser = subparsers.add_parser("facts", help="extract evidence-backed USB facts from a design model")
    facts_parser.add_argument("model", type=Path, help="path to a design-model JSON file")
    facts_parser.add_argument("--output", "-o", type=Path, help="write USB facts as JSON")
    unify_parser = subparsers.add_parser(
        "unify", help="join a saved PCB model with schematic symbols, pins, and nets"
    )
    unify_parser.add_argument("pcb_model", type=Path, help="path to a saved PCB design model JSON")
    unify_parser.add_argument("schematic", type=Path, help="path to the project .kicad_sch file")
    unify_parser.add_argument("--kicad-cli", type=Path, help="path to kicad-cli; defaults to PATH or KICAD_CLI")
    unify_parser.add_argument("--output", "-o", type=Path, help="write the unified model as JSON")
    review_parser = subparsers.add_parser("review", help="request a read-only AI engineering review")
    review_parser.add_argument("unified_model", type=Path, help="path to a V0.4 unified model JSON file")
    review_parser.add_argument("--model", default=os.environ.get("OLLAMA_MODEL"),
                               help="local Ollama model name (defaults to OLLAMA_MODEL)")
    review_parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
                               help="Ollama server URL (defaults to OLLAMA_HOST or localhost)")
    review_parser.add_argument("--timeout", type=float, default=900.0,
                               help="Ollama response timeout in seconds (default: 900)")
    review_parser.add_argument("--context-length", type=int, default=32768,
                               help="Ollama context window tokens (default: 32768)")
    review_parser.add_argument("--output", "-o", type=Path, help="write the structured review report as JSON")
    args = parser.parse_args(argv)

    if args.command == "inspect":
        return inspect(args.output)
    if args.command in {"analyze", "facts"}:
        try:
            model = json.loads(args.model.read_text(encoding="utf-8"))
            report = analyze_model(model) if args.command == "analyze" else extract_usb_facts(model)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Could not read design model {args.model}: {exc}", file=sys.stderr)
            return 2
        if args.output:
            try:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                temp_path = args.output.with_name(args.output.name + ".tmp")
                temp_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                temp_path.replace(args.output)
            except OSError as exc:
                print(f"Could not write report {args.output}: {exc}", file=sys.stderr)
                return 2
            print(f"Wrote {args.output}")
        else:
            rendered = render_report(report) if args.command == "analyze" else render_usb_facts(report)
            print(rendered, end="")
        return 0
    if args.command == "unify":
        try:
            pcb_model = json.loads(args.pcb_model.read_text(encoding="utf-8"))
            schematic_root = export_schematic_netlist(args.schematic, args.kicad_cli)
            unified = build_unified_model(pcb_model, schematic_root)
        except (OSError, json.JSONDecodeError, SchematicExportError) as exc:
            print(f"Could not build unified PCB + schematic model: {exc}", file=sys.stderr)
            return 2
        if args.output:
            try:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                temp_path = args.output.with_name(args.output.name + ".tmp")
                temp_path.write_text(json.dumps(unified, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                temp_path.replace(args.output)
            except OSError as exc:
                print(f"Could not write unified model {args.output}: {exc}", file=sys.stderr)
                return 2
            print(f"Wrote {args.output}")
        else:
            print(render_unified_report(unified), end="")
        return 0
    if args.command == "review":
        if not args.model:
            print("Choose an Ollama model with --model or set OLLAMA_MODEL.", file=sys.stderr)
            return 2
        try:
            unified = json.loads(args.unified_model.read_text(encoding="utf-8"))
            review_input = build_review_input(unified)
            report = OllamaProvider(
                model=args.model,
                base_url=args.ollama_url,
                timeout_seconds=args.timeout,
                context_length=args.context_length,
            ).review(review_input)
        except (OSError, json.JSONDecodeError, ValueError, ReviewProviderError) as exc:
            print(f"Could not review unified model {args.unified_model}: {exc}", file=sys.stderr)
            return 2
        serialized = json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n"
        if args.output:
            try:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                temp_path = args.output.with_name(args.output.name + ".tmp")
                temp_path.write_text(serialized, encoding="utf-8")
                temp_path.replace(args.output)
            except OSError as exc:
                print(f"Could not write review report {args.output}: {exc}", file=sys.stderr)
                return 2
            print(f"Wrote {args.output}")
        else:
            print(render_review_report(report.to_dict()), end="")
        return 0
    return 2


def render_review_report(report: dict) -> str:
    lines = ["AI KiCad Inspector — AI Engineering Review", "=" * 43]
    findings = report.get("findings", [])
    if not findings:
        lines.append("No review findings returned.")
    for index, finding in enumerate(findings, 1):
        references = ", ".join(item["id"] for item in finding["evidence"])
        lines.extend([
            "",
            f"{index}. [{finding['severity'].upper()}] {finding['claim']}",
            f"   Confidence: {finding['confidence']} | Evidence level: {finding['evidence_level']}",
            f"   Evidence: {references}",
            f"   Reasoning: {finding['reasoning']}",
            f"   Suggested action: {finding['suggested_action']}",
        ])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
