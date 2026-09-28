"""Uploaded images → small WebP thumbnails stored in the database, and the URLs that serve them.

<img> tags can't send the Authorization header, so image URLs are public but signed:
/api/comics/{id}/cover?v=<version>&sig=<hmac>. The signature (keyed by JWT_SECRET) only
comes from the owner's API responses, so the URL can't be guessed or enumerated. `v`
changes when the image changes, which lets browsers cache each URL forever.
"""

import base64
import binascii
import hashlib
import hmac
import io
import re
import uuid
from datetime import datetime

from fastapi import Request, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import get_settings
from app.core.errors import ApiError

# Vercel Functions accept request bodies up to 4.5 MB.
MAX_UPLOAD_BYTES = 4 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 40_000_000  # refuse decompression bombs

COVER_BOX = (480, 720)  # 2:3, fits the largest cover in the UI at 2x
AVATAR_SIZE = 256
WEBP_QUALITY = 80

_DATA_URL = re.compile(r"^data:image/[\w.+-]+;base64,(?P<data>.+)$", re.DOTALL)


def too_large() -> ApiError:
    return ApiError(413, "Ảnh lớn quá, nàng chọn ảnh dưới 4 MB nhé", "file_too_large")


def invalid_image() -> ApiError:
    return ApiError(400, "Tệp này không phải ảnh hợp lệ (PNG, JPG, WEBP, GIF)", "invalid_file")


async def read_upload(file: UploadFile) -> bytes:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise too_large()
    if not data:
        raise invalid_image()
    return data


def is_data_url(value: str) -> bool:
    return value.startswith("data:")


def decode_data_url(value: str) -> bytes:
    match = _DATA_URL.match(value.strip())
    if not match:
        raise invalid_image()
    if len(match["data"]) > MAX_UPLOAD_BYTES * 4 // 3 + 4:
        raise too_large()
    try:
        return base64.b64decode(match["data"], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise invalid_image() from exc


def _open(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError) as exc:
        raise invalid_image() from exc
    img = ImageOps.exif_transpose(img)  # phone photos: apply the EXIF rotation
    return img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB")


def _webp(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, "WEBP", quality=WEBP_QUALITY, method=4)  # no EXIF/metadata is written
    return out.getvalue()


def cover_thumbnail(data: bytes) -> bytes:
    img = _open(data)
    img.thumbnail(COVER_BOX, Image.Resampling.LANCZOS)  # keeps the aspect ratio, never upscales
    return _webp(img)


def avatar_thumbnail(data: bytes) -> bytes:
    img = ImageOps.fit(_open(data), (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)  # centre crop
    return _webp(img)


# ── URLs and responses ────────────────────────────────────────────────────


def _version(updated_at: datetime) -> str:
    return str(int(updated_at.timestamp() * 1000))


def _signature(kind: str, owner_id: uuid.UUID, version: str) -> str:
    key = get_settings().jwt_secret.encode()
    return hmac.new(key, f"{kind}:{owner_id}:{version}".encode(), hashlib.sha256).hexdigest()[:32]


def media_url(kind: str, owner_id: uuid.UUID, updated_at: datetime) -> str:
    """kind: "cover" (owner = comic) or "avatar" (owner = user). Relative, like every API path."""
    base = f"/api/comics/{owner_id}/cover" if kind == "cover" else f"/api/users/{owner_id}/avatar"
    version = _version(updated_at)
    return f"{base}?v={version}&sig={_signature(kind, owner_id, version)}"


def verify_media(kind: str, owner_id: uuid.UUID, updated_at: datetime | None, v: str, sig: str) -> bool:
    if updated_at is None or v != _version(updated_at):
        return False
    return hmac.compare_digest(sig, _signature(kind, owner_id, v))


def image_response(request: Request, data: bytes, version: str) -> Response:
    etag = f'"{version}"'
    headers = {
        # the URL changes with every new image, so each one can be cached for good
        "Cache-Control": "public, max-age=31536000, immutable",
        "ETag": etag,
        "X-Content-Type-Options": "nosniff",
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=data, media_type="image/webp", headers=headers)
