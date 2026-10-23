"""Small text helpers: slugs, titles, excerpts."""

import re
import unicodedata
from pathlib import PurePath

# A date opening a file name ("2026-08-26 Titel") sets its publication date.
DATE_PREFIX = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[\s._-]+")

# A number opening a name ("01 Projecten") sets the order of a folder or file and is not shown.
NUMBER_PREFIX = re.compile(r"^(\d+)[\s._-]+")

# File names cameras and phones generate, which make poor titles.
CAMERA_NAME = re.compile(r"^(img|dsc|dscn|dscf|pxl|mvimg|photo|image|screenshot)[\W_]*\d", re.IGNORECASE)


def ucfirst(text: str) -> str:
    """The first letter in capitals, the rest as written ("iPhone tips" stays as it is but for the i)."""
    return text[:1].upper() + text[1:]


def slugify(text: str, fallback: str = "item") -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:80].rstrip("-") or fallback


def split_date(name: str) -> tuple[tuple[int, int, int] | None, str]:
    """The (year, month, day) a file name opens with, if any, and the name without it."""
    m = DATE_PREFIX.match(name)
    if not m:
        return None, name
    return (int(m[1]), int(m[2]), int(m[3])), name[m.end() :]


def split_number(name: str) -> tuple[int | None, str]:
    """The number a name opens with, if any, and the name without it."""
    m = NUMBER_PREFIX.match(name)
    return (int(m[1]), name[m.end() :]) if m else (None, name)


def display_name(name: str) -> str:
    """A file or folder name as shown: without a date or number prefix."""
    return split_number(split_date(name)[1])[1]


def photo_title(filename: str) -> str | None:
    """A title from an image's file name, or None for camera-generated names."""
    stem = PurePath(display_name(filename)).stem
    if CAMERA_NAME.match(stem) or not re.search(r"[A-Za-z]", stem):
        return None
    return ucfirst(re.sub(r"[_-]+", " ", stem).strip())


def plain(tag) -> str:
    """An HTML element's text as one line. Its pieces are joined as they are, so "*site*," stays
    "site," (not "site ,"); a line break counts as a space."""
    parts = [
        " " if getattr(piece, "name", None) == "br" else piece
        for piece in tag.descendants
        if isinstance(piece, str) or getattr(piece, "name", None) == "br"
    ]
    return " ".join("".join(parts).split())


def excerpt(text: str, length: int = 220) -> str:
    text = " ".join(text.split())
    if len(text) <= length:
        return text
    cut = text[:length]
    # End on a whole sentence when one ends in the second half; else on a word.
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end > length // 2:
        return cut[: end + 1]
    return cut.rsplit(" ", 1)[0].rstrip(",.;:") + "…"
