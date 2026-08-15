"""Image acquisition and analysis.

Downloads candidate images to local storage and extracts the low-level signals
the rest of the pipeline relies on:

* perceptual hash (pHash) for near-duplicate detection,
* dimensions / aspect ratio,
* OCR text (optional; used by the safety text screen and taste analysis).

OCR is optional: if ``pytesseract`` + the ``tesseract-ocr`` binary are not
installed, OCR returns ``(text="", available=False)`` and the safety layer
decides how to treat that based on the ``require_ocr`` setting (fail-closed).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import get_config

try:  # Pillow + ImageHash are hard requirements, imported lazily for clarity.
    from PIL import Image, ImageFile

    ImageFile.LOAD_TRUNCATED_IMAGES = False
    _PIL_OK = True
except Exception:  # pragma: no cover - import guard
    _PIL_OK = False

try:
    import imagehash

    _IMAGEHASH_OK = True
except Exception:  # pragma: no cover
    _IMAGEHASH_OK = False

# OCR is optional.
try:
    import pytesseract  # type: ignore

    _PYTESSERACT_IMPORTED = True
except Exception:  # pragma: no cover
    _PYTESSERACT_IMPORTED = False


MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024  # 15 MB hard cap
ALLOWED_FORMATS = {"JPEG", "PNG", "GIF", "WEBP", "MPO"}


@dataclass
class ImageInfo:
    local_path: str
    phash: str
    width: int
    height: int
    fmt: str
    ocr_text: str
    ocr_available: bool
    text_density: float  # rough 0-1 fraction of pixels that look like text


class ImageError(Exception):
    pass


def _safe_name(url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    return digest


def download(url: str) -> Path:
    """Stream an image to local storage with a hard size cap."""
    if not _PIL_OK:
        raise ImageError("Pillow is not installed")
    cfg = get_config()
    dest = cfg.images_path / f"{_safe_name(url)}"
    headers = {"User-Agent": cfg.user_agent}
    try:
        with httpx.Client(headers=headers, timeout=30.0, follow_redirects=True) as client:
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
                ctype = resp.headers.get("content-type", "")
                if ctype and not ctype.startswith("image/"):
                    raise ImageError(f"not an image (content-type={ctype})")
                total = 0
                with open(dest, "wb") as fh:
                    for chunk in resp.iter_bytes(65536):
                        total += len(chunk)
                        if total > MAX_DOWNLOAD_BYTES:
                            fh.close()
                            dest.unlink(missing_ok=True)
                            raise ImageError("image exceeds size cap")
                        fh.write(chunk)
    except httpx.HTTPError as exc:
        # A dead/removed image (404, timeout, etc.) must not crash the whole
        # discovery cycle -- surface it as ImageError so the caller skips it.
        dest.unlink(missing_ok=True)
        raise ImageError(f"download failed: {exc}") from exc
    return dest


def _compute_ocr(img: "Image.Image") -> tuple[str, bool, float]:
    if not _PYTESSERACT_IMPORTED:
        return "", False, 0.0
    try:
        text = pytesseract.image_to_string(img) or ""
    except Exception:
        return "", False, 0.0
    text = text.strip()
    # crude density: characters per kilopixel, clamped to 0..1
    kilopixels = max((img.width * img.height) / 1000.0, 1.0)
    density = min(len(text) / kilopixels, 1.0)
    return text, True, density


def analyze(url: str) -> ImageInfo:
    """Download + analyze an image. Raises ImageError on any problem."""
    if not (_PIL_OK and _IMAGEHASH_OK):
        raise ImageError("Pillow/ImageHash not installed")

    path = download(url)
    try:
        with Image.open(path) as img:
            img.verify()  # detect truncated/corrupt files
        with Image.open(path) as img:
            fmt = (img.format or "").upper()
            if fmt not in ALLOWED_FORMATS:
                raise ImageError(f"unsupported format: {fmt}")
            rgb = img.convert("RGB")
            width, height = rgb.size
            if width < 200 or height < 200:
                raise ImageError("image too small")
            phash = str(imagehash.phash(rgb))
            ocr_text, ocr_available, density = _compute_ocr(rgb)
    except ImageError:
        path.unlink(missing_ok=True)
        raise
    except Exception as exc:  # corrupt / unreadable
        path.unlink(missing_ok=True)
        raise ImageError(f"could not analyze image: {exc}") from exc

    return ImageInfo(
        local_path=str(path),
        phash=phash,
        width=width,
        height=height,
        fmt=fmt,
        ocr_text=ocr_text,
        ocr_available=ocr_available,
        text_density=density,
    )


def hamming(a: str, b: str) -> int:
    """Hamming distance between two hex pHash strings (large = different)."""
    if not a or not b or len(a) != len(b):
        return 999
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return 999


def ocr_available() -> bool:
    return _PYTESSERACT_IMPORTED
