"""One bounded JPEG preview codec shared by authorized screenshot actions.

Pure presentation: callers retain their own project/device policy and own the
original image. Pillow stays lazy so a Native Runtime without it can import
the action catalog. No additional capture, backend or storage is created.
"""
from __future__ import annotations

import base64
import hashlib
import io
from typing import Any


def bounded_jpeg_preview(image: Any) -> dict[str, Any]:
    """Encode one already-captured PIL image in <=256 KiB, or reject it."""
    from PIL import Image

    preview = image.copy()
    preview.thumbnail((960, 720), Image.Resampling.LANCZOS)
    if preview.mode != "RGB":
        if "A" in preview.getbands() or "transparency" in preview.info:
            rgba = preview.convert("RGBA")
            opaque = Image.new("RGB", rgba.size, "white")
            opaque.paste(rgba, mask=rgba.getchannel("A"))
            preview = opaque
        else:
            preview = preview.convert("RGB")
    jpeg = b""
    for quality in (72, 58, 42, 30):
        buffer = io.BytesIO()
        preview.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
        jpeg = buffer.getvalue()
        if len(jpeg) <= 262144:
            break
        preview.thumbnail(
            (max(64, preview.width * 3 // 4), max(64, preview.height * 3 // 4)),
            Image.Resampling.LANCZOS,
        )
    if not jpeg or len(jpeg) > 262144:
        raise ValueError("screenshot preview byte budget exceeded")
    return {
        "size_bytes": len(jpeg),
        "sha256": hashlib.sha256(jpeg).hexdigest(),
        "mime_type": "image/jpeg",
        "thumbnail": True,
        "thumbnail_size": [preview.width, preview.height],
        "base64": base64.b64encode(jpeg).decode("ascii"),
    }
