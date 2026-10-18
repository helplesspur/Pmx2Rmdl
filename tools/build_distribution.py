"""Build the public PyInstaller layout from this repository.

This script only builds local executables; it never copies game or model assets.
"""
from pathlib import Path
import subprocess, sys

ROOT = Path(__file__).resolve().parents[1]
cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
       "--onedir", "--name", "pmxconv", "--paths", str(ROOT / "src" / "resources"),
       "--add-data", f"{ROOT / 'src' / 'resources'};resources",
       "--hidden-import", "integration", "--hidden-import", "rigs",
       "--hidden-import", "runtime_audit", "--hidden-import", "texture_rules",
       "--hidden-import", "menu_models", "--hidden-import", "rpak_index",
       "--hidden-import", "PIL.Image", "--hidden-import", "numpy",
       str(ROOT / "src" / "pmxconv_gui.py")]
subprocess.run(cmd, cwd=ROOT, check=True)
