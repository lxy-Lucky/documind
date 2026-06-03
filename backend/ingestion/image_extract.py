"""Extract images embedded inside an Excel sheet.

openpyxl exposes `Worksheet._images` (a list of `openpyxl.drawing.image.Image`)
once the workbook is loaded. Each image has an anchor describing the cell
range it sits over; we serialize the bytes to disk and return descriptors
for the ingestion pipeline to attach to the `image` table.

The extracted PNGs are later fed to the VL model for description.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from openpyxl.drawing.image import Image as XLImage
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from PIL import Image as PILImage


@dataclass
class ExtractedImage:
    file_path: Path
    anchor_cell: str          # 'B5:K30' or 'B5' for one-cell anchors
    width: int
    height: int


def _anchor_to_range(img: XLImage) -> str:
    """Convert openpyxl anchor into an Excel A1 range string."""
    anc = img.anchor
    if anc is None:
        return ""

    def _frm(a) -> str:
        return f"{get_column_letter(a.col + 1)}{a.row + 1}"

    if hasattr(anc, "_from") and getattr(anc, "to", None):
        return f"{_frm(anc._from)}:{_frm(anc.to)}"
    if hasattr(anc, "_from"):
        return _frm(anc._from)
    return ""


def _image_bytes(img: XLImage) -> bytes:
    """openpyxl's Image.ref can be a PIL.Image, a BytesIO, or a path.
    Normalize to raw bytes."""
    ref = img.ref
    if isinstance(ref, (bytes, bytearray)):
        return bytes(ref)
    if hasattr(ref, "read"):
        ref.seek(0)
        return ref.read()
    if isinstance(ref, PILImage.Image):
        from io import BytesIO
        buf = BytesIO()
        ref.save(buf, format="PNG")
        return buf.getvalue()
    if isinstance(ref, (str, Path)):
        return Path(ref).read_bytes()
    raise TypeError(f"Unsupported image ref type: {type(ref)!r}")


def extract_sheet_images(ws: Worksheet, out_dir: Path) -> list[ExtractedImage]:
    """Persist every image embedded in `ws` to PNG and return descriptors.

    File naming: `<sha1[:12]>.png` — content-addressed so duplicate
    images are dedup'd across sheets.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    images: list[XLImage] = list(getattr(ws, "_images", []) or [])
    out: list[ExtractedImage] = []

    for idx, img in enumerate(images):
        try:
            raw = _image_bytes(img)
        except Exception as e:
            logger.warning(f"Skipping image #{idx} in sheet '{ws.title}': {e}")
            continue

        digest = hashlib.sha1(raw).hexdigest()[:12]
        ext = (img.format or "png").lower()
        if ext not in ("png", "jpg", "jpeg", "gif", "bmp"):
            ext = "png"
        path = out_dir / f"{digest}.{ext}"
        if not path.exists():
            path.write_bytes(raw)

        # If not PNG, also write a PNG copy for downstream tools that
        # only accept PNG. (VL ingest is happy with jpg/png both, so we
        # skip this for now — leave the original format alone.)
        width, height = (img.width or 0, img.height or 0)
        try:
            with PILImage.open(path) as p:
                width, height = p.size
        except Exception:
            pass

        out.append(ExtractedImage(
            file_path=path,
            anchor_cell=_anchor_to_range(img),
            width=int(width),
            height=int(height),
        ))

    return out
