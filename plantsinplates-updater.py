"""Run the shared updater without loading the scientific application."""

import pathlib
import runpy
import sys

if __name__ == "__main__":
    root = pathlib.Path(__file__).resolve().parent
    if not (root / "plantsinplates" / "updates.py").is_file():
        sys.path.insert(0, str(root / "plantsinplates-app"))
    sys.argv[1:1] = ["--root", str(root)]
    runpy.run_module("plantsinplates.updates", run_name="__main__", alter_sys=True)
