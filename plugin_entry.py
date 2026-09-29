"""KiCad IPC action shim; KiCad's launcher accepts a script path, not a CLI."""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ai_kicad.cli import main


raise SystemExit(main(["inspect"]))
