"""File storage abstraction.

Two backends:
  - 'local' writes to a directory on disk. Files served by Flask via /uploads/.
    Good for dev. Not recommended for prod (no CDN, lost on container restart).
  - 's3' uploads to S3-compatible storage (AWS S3, Cloudflare R2, MinIO).
    Good for prod. Files served from STORAGE_S3_PUBLIC_BASE.

Both backends return:
  - storage_key: the path/key in the underlying store
  - public_url: full URL the customer/trade can fetch
  - thumbnail_url: same but for the resized thumbnail (if generated)

Photos are auto-resized to a max width and a thumbnail before storage.
"""
from __future__ import annotations

import logging
import os
import secrets
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from flask import current_app

logger = logging.getLogger(__name__)

# Allowlist content types — defensive, since we accept user uploads
ALLOWED_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/heic": "heic",
    "image/heif": "heif",
    "image/webp": "webp",
}


class StorageError(Exception):
    pass


def _key_for(prefix: str, ext: str) -> str:
    """Generate a random storage key like 'bookings/<random>.jpg'."""
    rand = secrets.token_urlsafe(16)
    return f"{prefix}/{rand}.{ext}"


def _resize_image(data: bytes, max_dim: int = 1600, quality: int = 82) -> tuple[bytes, str, int, int]:
    """Resize image to fit within max_dim x max_dim, preserving aspect ratio.

    Returns (jpeg_bytes, content_type, width, height).
    HEIC/HEIF inputs are converted to JPEG.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:
        raise StorageError("Pillow is required for image processing") from None

    img = Image.open(BytesIO(data))
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")
    img.thumbnail((max_dim, max_dim))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue(), "image/jpeg", img.width, img.height


def store_image(file_stream: BinaryIO, content_type: str, prefix: str = "bookings") -> dict:
    """Take an uploaded image stream, resize, store, return storage metadata.

    Validates content type, size; rejects oversized.
    """
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise StorageError(f"Unsupported image type: {content_type}")

    max_bytes = current_app.config.get("MAX_PHOTO_BYTES", 10 * 1024 * 1024)
    raw = file_stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise StorageError(
            f"Image too large (max {max_bytes // 1024 // 1024} MB)"
        )

    # Resize main + thumb
    main_bytes, main_ct, w, h = _resize_image(raw, max_dim=1600, quality=82)
    thumb_bytes, _, _, _ = _resize_image(raw, max_dim=400, quality=78)

    main_key = _key_for(prefix, "jpg")
    thumb_key = main_key.replace(".jpg", "-thumb.jpg")

    main_url = _put(main_key, main_bytes, "image/jpeg")
    thumb_url = _put(thumb_key, thumb_bytes, "image/jpeg")

    return {
        "storage_key": main_key,
        "public_url": main_url,
        "thumbnail_url": thumb_url,
        "content_type": main_ct,
        "width": w,
        "height": h,
        "size_bytes": len(main_bytes),
    }


def _put(key: str, data: bytes, content_type: str) -> str:
    backend = current_app.config.get("STORAGE_BACKEND", "local")
    if backend == "local":
        return _put_local(key, data)
    if backend == "s3":
        return _put_s3(key, data, content_type)
    raise StorageError(f"Unknown STORAGE_BACKEND: {backend}")


def _put_local(key: str, data: bytes) -> str:
    """Write to local disk under STORAGE_LOCAL_DIR."""
    base = Path(current_app.config["STORAGE_LOCAL_DIR"])
    full = base / key
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(data)
    # Return relative URL the local-uploads route serves from
    return f"/uploads/{key}"


def _put_s3(key: str, data: bytes, content_type: str) -> str:
    try:
        import boto3
    except ImportError:
        raise StorageError("boto3 is required for S3 storage") from None

    bucket = current_app.config["STORAGE_S3_BUCKET"]
    region = current_app.config.get("STORAGE_S3_REGION", "auto")
    endpoint = current_app.config.get("STORAGE_S3_ENDPOINT") or None
    public_base = current_app.config["STORAGE_S3_PUBLIC_BASE"].rstrip("/")
    access_key = current_app.config["STORAGE_S3_ACCESS_KEY"]
    secret_key = current_app.config["STORAGE_S3_SECRET_KEY"]

    s3 = boto3.client(
        "s3",
        region_name=region,
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
        # Keep things simple and safe — public-read isn't always available on R2.
        # For R2: rely on bucket-level public access toggle.
    )
    return f"{public_base}/{key}"


def delete_object(storage_key: str) -> None:
    """Best-effort delete. Used when removing a photo or after session end."""
    backend = current_app.config.get("STORAGE_BACKEND", "local")
    try:
        if backend == "local":
            base = Path(current_app.config["STORAGE_LOCAL_DIR"])
            (base / storage_key).unlink(missing_ok=True)
            (base / storage_key.replace(".jpg", "-thumb.jpg")).unlink(missing_ok=True)
        elif backend == "s3":
            import boto3

            bucket = current_app.config["STORAGE_S3_BUCKET"]
            endpoint = current_app.config.get("STORAGE_S3_ENDPOINT") or None
            s3 = boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=current_app.config["STORAGE_S3_ACCESS_KEY"],
                aws_secret_access_key=current_app.config["STORAGE_S3_SECRET_KEY"],
            )
            s3.delete_object(Bucket=bucket, Key=storage_key)
            s3.delete_object(Bucket=bucket, Key=storage_key.replace(".jpg", "-thumb.jpg"))
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to delete %s: %s", storage_key, e)


def serve_local_route(app):
    """Register the /uploads/<path> route for local dev backend.

    In prod the public_url points at S3/R2 directly so this isn't used.
    """
    from flask import abort, send_from_directory

    @app.route("/uploads/<path:key>")
    def _serve_upload(key: str):
        if app.config.get("STORAGE_BACKEND") != "local":
            abort(404)
        return send_from_directory(app.config["STORAGE_LOCAL_DIR"], key)
