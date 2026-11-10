"""Reading a scan out of a WhatsApp message.

The counter QR pre-types "Hey Notchs!, get me notched. KAHWA-482913", but people edit messages and
phones change them, so the reading is forgiving:

    "Hey Notchs!, get me notched. KAHWA-482913"   -> Scan("KAHWA", "482913")
    "kahwa - 482913", "KAHWA 482913", "🙏 kahwa-482913 thanks" -> the same
    "Hey Notchs!, get me notched."                -> NoCode() (a QR away from a venue: no stamp)
    "hi", "LOGIN"                                 -> None (not a scan)

The scan code found here is only a candidate: whether such a venue exists is checked later.
"""

import re
from dataclasses import dataclass

LIVE_CODE_DIGITS = 6

# A scan code (letters and digits, starting with a letter) right before 6 digits, with an optional
# hyphen or dash and spaces between them. Not inside a longer word or number.
# Hyphen-like characters autocorrect puts in place of '-': hyphen, non-breaking hyphen, en and
# em dash. Written by number so the source stays plain ASCII.
_DASHES = "".join(map(chr, (0x2010, 0x2011, 0x2013, 0x2014)))
_SCAN = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]{2,9})"
    rf"\s*[-{_DASHES}_]?\s*"
    r"([0-9]{6})(?![0-9])"
)
# "get me notched" in any case, with any spacing or punctuation between the words.
_PHRASE = re.compile(r"get\W*me\W*notch(?:ed)?", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Scan:
    scan_code: str  # uppercase
    live_code: str  # 6 digits


@dataclass(frozen=True, slots=True)
class NoCode:
    """The scan sentence without a code: from a QR that is not at a venue."""


def parse(text: str | None) -> Scan | NoCode | None:
    if not text:
        return None
    match = _SCAN.search(text)
    if match is not None:
        return Scan(scan_code=match.group(1).upper(), live_code=match.group(2))
    if _PHRASE.search(text):
        return NoCode()
    return None
