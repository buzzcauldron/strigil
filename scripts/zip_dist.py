"""Zip dist/strigil into dist/strigil-win.zip. Used by build_windows.bat."""
import zipfile
from pathlib import Path

def main():
    root = Path(__file__).resolve().parent.parent
    src = root / "dist" / "strigil"
    out = root / "dist" / "strigil-win.zip"
    if not src.is_dir():
        raise SystemExit(f"Missing {src}")
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in src.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(src.parent))
    print(f"Done: {out}")

if __name__ == "__main__":
    main()
