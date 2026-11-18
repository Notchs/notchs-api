"""Customer profile pictures: checked, cleaned and resized before they are stored.

- Only real JPG, PNG or WebP images up to MAX_UPLOAD_BYTES (the file is opened, not trusted).
- Turned the right way up, then every bit of metadata is dropped: phone photos often carry the
  GPS location where they were taken.
- Cropped to a centred square and resized to PHOTO_SIZE, saved as WebP.
"""

import io
import secrets
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
PHOTO_SIZE = 512
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
# Refuse absurd dimensions before decoding (a tiny file can claim a huge picture).
MAX_PIXELS = 40_000_000
CONTENT_TYPE = "image/webp"


class PhotoInvalidError(ValueError):
    pass


def photo_key(user_id: uuid.UUID) -> str:
    """A new name on every upload, so an old signed link never shows the new photo."""
    return f"users/{user_id}/photo-{secrets.token_hex(8)}.webp"


def clean_photo(data: bytes) -> bytes:
    """The stored version: square, PHOTO_SIZE wide, WebP, no metadata. Raises PhotoInvalidError."""
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise PhotoInvalidError("size")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in ALLOWED_FORMATS:
                raise PhotoInvalidError("format")
            width, height = probe.size
            if width * height > MAX_PIXELS:
                raise PhotoInvalidError("dimensions")
            probe.verify()  # truncated or corrupt files fail here
        with Image.open(io.BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            image = image.convert("RGB")
            image = ImageOps.fit(image, (PHOTO_SIZE, PHOTO_SIZE), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            # A fresh image from pixels only: no EXIF, GPS or other metadata is written.
            Image.frombytes("RGB", image.size, image.tobytes()).save(
                out, format="WEBP", quality=85, method=4
            )
            return out.getvalue()
    except PhotoInvalidError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise PhotoInvalidError("unreadable") from exc
