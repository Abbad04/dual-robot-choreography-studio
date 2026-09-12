"""Repository launcher for the local FR5 browser connector."""

from pathlib import Path
import runpy
import sys


if __name__ == "__main__":
    source_root = Path(__file__).resolve().parent / "src"
    sys.path.insert(0, str(source_root))
    runpy.run_module("ur20_timing.fr5_live_bridge", run_name="__main__")
