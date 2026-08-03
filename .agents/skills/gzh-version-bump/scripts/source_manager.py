#!/usr/bin/env python3
"""Forward the legacy source-manager entry point to the shared core."""

from pathlib import Path
import runpy


TARGET = (
    Path(__file__).resolve().parents[2]
    / "gentoo-overlay-development" / "scripts" / "source_manager.py"
)


if __name__ == "__main__":
    runpy.run_path(str(TARGET), run_name="__main__")
