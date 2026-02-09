# PyInstaller spec: build CLI and GUI into one folder (dist/strigil/).
# Run from project root: pyinstaller strigil.spec
# Requires: pip install strigil[bundle]

import os

SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
PROJECT_ROOT = SPEC_DIR

hidden_imports = [
    "strigil",
    "strigil.cli",
    "strigil.gui",
    "strigil._deps",
    "httpx",
    "bs4",
    "lxml",
]

# --- CLI ---
cli_script = os.path.join(PROJECT_ROOT, "scripts", "run_cli.py")
a_cli = Analysis(
    [cli_script],
    pathex=[PROJECT_ROOT],
    hiddenimports=hidden_imports,
)
pyz_cli = PYZ(a_cli.pure, a_cli.zipped_data)
exe_cli = EXE(
    pyz_cli,
    a_cli.scripts,
    [],
    exclude_binaries=True,
    name="strigil",
)

# --- GUI ---
gui_script = os.path.join(PROJECT_ROOT, "scripts", "run_gui.py")
a_gui = Analysis(
    [gui_script],
    pathex=[PROJECT_ROOT],
    hiddenimports=hidden_imports,
)
pyz_gui = PYZ(a_gui.pure, a_gui.zipped_data)
exe_gui = EXE(
    pyz_gui,
    a_gui.scripts,
    [],
    exclude_binaries=True,
    name="strigil-gui",
)

# One folder with both executables so the GUI can run the CLI
coll = COLLECT(
    exe_cli,
    exe_gui,
    a_cli.binaries,
    a_cli.datas,
    a_gui.binaries,
    a_gui.datas,
    name="strigil",
)
