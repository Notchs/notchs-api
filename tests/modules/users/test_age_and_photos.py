"""Age ranges from a date of birth, and how profile pictures are cleaned before storage."""

import io
from datetime import date

import pytest
from PIL import Image

from app.modules.users.age import age_on, age_range
from app.modules.users.photos import PHOTO_SIZE, PhotoInvalidError, clean_photo

TODAY = date(2026, 11, 18)


@pytest.mark.parametrize(
    ("born", "age", "expected"),
    [
        (date(2008, 11, 18), 18, "18-24"),  # 18th birthday today
        (date(2008, 11, 19), 17, None),  # one day short: never saved
        (date(2001, 11, 19), 24, "18-24"),
        (date(2001, 11, 18), 25, "25-34"),
        (date(1982, 1, 1), 44, "35-44"),
        (date(1981, 11, 18), 45, "45+"),
        (date(1950, 6, 1), 76, "45+"),
    ],
)
def test_age_and_range(born: date, age: int, expected: str | None) -> None:
    assert age_on(born, TODAY) == age
    assert age_range(born, TODAY) == expected


def test_no_birthday_no_range() -> None:
    assert age_range(None, TODAY) is None


def test_born_on_29_february() -> None:
    assert age_on(date(2004, 2, 29), date(2026, 2, 28)) == 21
    assert age_on(date(2004, 2, 29), date(2026, 3, 1)) == 22


def _jpeg_with_gps(size: tuple[int, int]) -> bytes:
    exif = Image.Exif()
    exif[0x8825] = {1: "N", 2: (23.0, 2.0, 0.0)}
    out = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(out, format="JPEG", exif=exif)
    return out.getvalue()


def test_cleaned_picture_is_a_small_square_webp_without_metadata() -> None:
    cleaned = clean_photo(_jpeg_with_gps((1200, 400)))

    with Image.open(io.BytesIO(cleaned)) as image:
        assert (image.format, image.size) == ("WEBP", (PHOTO_SIZE, PHOTO_SIZE))
        assert not image.getexif()


def test_a_truncated_picture_is_refused() -> None:
    with pytest.raises(PhotoInvalidError):
        clean_photo(_jpeg_with_gps((200, 200))[:300])
