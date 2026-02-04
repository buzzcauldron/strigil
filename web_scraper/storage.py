"""Path building, filename sanitization, and file writing."""

import json
import re
from pathlib import Path
from urllib.parse import urlparse

OUTPUT_STRUCTURE = "output/<domain>/pdfs|images|texts"


def sanitize_domain(url: str) -> str:
    """Extract and sanitize domain from URL for directory name."""
    parsed = urlparse(url)
    domain = parsed.netloc or "unknown"
    domain = re.sub(r"[^\w.-]", "_", domain)
    return domain or "unknown"


def sanitize_basename(url: str, default_ext: str = "") -> str:
    """
    Sanitize URL to a safe filename: strip query, replace path chars with _,
    avoid collisions with numeric suffix.
    """
    parsed = urlparse(url)
    path = parsed.path or "/"
    name = path.split("/")[-1] or "index"
    name = name.split("?")[0]
    name = re.sub(r"[^\w.-]", "_", name)
    name = name.strip("_") or "file"
    if len(name) > 200:
        name = name[:200]
    if default_ext and not (name.lower().endswith(f".{default_ext}") or "." in name):
        name = f"{name}.{default_ext}"
    return name


def slug_from_url(url: str) -> str:
    """Create a slug for a page URL (for texts/)."""
    parsed = urlparse(url)
    path = parsed.path or "/"
    parts = [p for p in path.split("/") if p]
    slug = "_".join(parts) if parts else "index"
    slug = re.sub(r"[^\w.-]", "_", slug)
    slug = slug.strip("_") or "index"
    if len(slug) > 150:
        slug = slug[:150]
    return slug


def _ensure_unique(path: Path) -> Path:
    """If path exists, add numeric suffix to avoid overwrite."""
    if not path.exists():
        return path
    stem = path.stem
    ext = path.suffix
    parent = path.parent
    n = 1
    while True:
        candidate = parent / f"{stem}_{n}{ext}"
        if not candidate.exists():
            return candidate
        n += 1


def path_for_pdf(out_dir: Path, domain: str, url: str) -> Path:
    """Return full path for a PDF file."""
    base = sanitize_basename(url, "pdf")
    p = out_dir / domain / "pdfs" / base
    return _ensure_unique(p)


def path_for_image(out_dir: Path, domain: str, url: str, content_type: str | None = None) -> Path:
    """Return full path for an image. Infer extension from URL or Content-Type."""
    ext = ""
    if content_type:
        m = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp", "image/svg+xml": "svg"}
        ext = m.get(content_type, "")
    parsed = urlparse(url)
    path = parsed.path.lower()
    if not ext:
        for e in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".ico"):
            if path.endswith(e):
                ext = e.lstrip(".")
                break
    ext = ext or "bin"
    base = sanitize_basename(url, ext)
    if not base.lower().endswith(f".{ext}"):
        base = f"{base}.{ext}" if "." not in base else base
    p = out_dir / domain / "images" / base
    return _ensure_unique(p)


def path_for_text(out_dir: Path, domain: str, url: str) -> Path:
    """Return full path for extracted text."""
    slug = slug_from_url(url)
    base = f"{slug}.txt"
    p = out_dir / domain / "texts" / base
    return _ensure_unique(p)


def write_text(path: Path, text: str) -> None:
    """Write text as UTF-8."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_binary(path: Path, data: bytes) -> None:
    """Write binary data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def manifest_path(out_dir: Path, domain: str) -> Path:
    """Path for optional manifest.json."""
    return out_dir / domain / "manifest.json"


def load_manifest(path: Path) -> dict:
    """Load manifest if exists."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_manifest(path: Path, manifest: dict) -> None:
    """Save manifest JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def url_in_manifest(manifest: dict, url: str, key: str = "urls") -> bool:
    """Check if URL is already recorded (for skip-if-exists)."""
    urls = manifest.get(key, {})
    return url in urls
