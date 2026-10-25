"""The kinds of file a site is made from, whichever source they come from: what each one is, how
to recognize it, and how to read a document of that kind.

There are two kinds. A document (a Google Doc, a Word file) is a page of text; an image is a
photo. A document's format reads the file into clean HTML (the tags convert.py lists, nothing
else: no styles, no classes) with its title and subtitle, and says where its images come from.
That is all that sets one format apart: convert.convert() then makes every document a page the
same way, so a heading or a table looks the same on the site whatever it was written in.

Adding a format is a Document below, with the types and extensions it is known by, a read() and a
line in FORMATS; the reading itself goes in a module of its own in this folder (gdoc.py, word.py),
imported only when a file of that format is read. Nothing else names a format: the sources list
what format_for() recognizes. tests/test_formats.py checks every format against this contract.
"""

from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from bs4 import Tag

    from ..sources import Entry, Source

DOC = "application/vnd.google-apps.document"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
# The images published: photos, screenshots and GIFs. Others (SVG, PSD, camera RAW) are left out.
IMAGE_TYPES = frozenset(f"image/{t}" for t in ("jpeg", "png", "webp", "gif", "heic", "heif"))
# What a server says of a file it doesn't know the type of: then its extension says.
UNTYPED = frozenset({"", "text/plain", "application/octet-stream"})


@dataclass
class Reading:
    """A document being read: the file, the source it comes from, and every file in the folder by
    its folders' names and its own (hidden ones too), for a document that shows another file."""

    entry: Entry
    source: Source
    tree: dict[tuple[str, ...], Entry] = field(default_factory=dict)

    def download(self) -> bytes:
        return self.source.download(self.entry)


@dataclass
class DocText:
    """A document as its format reads it."""

    html: str  # clean HTML, for convert()
    image: Callable[[str], bytes]  # an <img> src in it, as the image; raises if it can't be had
    title: str | None = None  # where the document names one (the Title style); else convert looks for it
    subtitle: str | None = None


class Format(ABC):
    """A kind of file the site is made from."""

    name: ClassVar[str]  # short, as an entry records it: "gdoc", "word", "image"
    kind: ClassVar[str]  # "doc" (a page of text) or "image" (a photo)
    label: ClassVar[str]  # as people know it, for messages: "a Word file"
    mimes: ClassVar[frozenset[str]]  # the types a source lists it as
    extensions: ClassVar[tuple[str, ...]] = ()  # lower case; left out of its title, and say it when the type doesn't

    def accepts(self, mime: str, name: str) -> bool:
        """Whether a file of this type and name is one."""
        by_name = bool(self.extensions) and name.lower().endswith(self.extensions)
        return mime in self.mimes or (mime in UNTYPED and by_name)

    def stem(self, name: str) -> str:
        """The name without its extension, for titles and roles."""
        lower = name.lower()
        return next((name[: -len(ext)] for ext in self.extensions if lower.endswith(ext)), name)


class Document(Format):
    """A page of text."""

    kind = "doc"

    @abstractmethod
    def read(self, file: Reading) -> DocText:
        """The document, read from its source."""


class GoogleDoc(Document):
    """A Doc in Google's own format: the service exports it as HTML."""

    name, label = "gdoc", "a Google Doc"
    mimes = frozenset({DOC})

    def read(self, file: Reading) -> DocText:
        from . import gdoc

        return gdoc.read(file)


class WordFile(Document):
    """A Word file (.docx), from any source; its images come along inside it."""

    name, label = "word", "a Word file"
    mimes = frozenset({DOCX})
    extensions = (".docx",)

    def read(self, file: Reading) -> DocText:
        from . import word

        return word.read(file.download())


class Image(Format):
    """A photo, a screenshot or a GIF: a page of its own, or a portrait."""

    name, kind, label = "image", "image", "an image"
    mimes = IMAGE_TYPES


# Every format, in the order they are tried.
FORMATS: tuple[Format, ...] = (GoogleDoc(), WordFile(), Image())


def format_for(mime: str, name: str = "") -> Format | None:
    """The format of a file of this type and name, or None if it is none the site publishes."""
    return next((f for f in FORMATS if f.accepts(mime, name)), None)


BY_NAME = {f.name: f for f in FORMATS}


def named(name: str) -> Format:
    """A format by its name, as an entry records it."""
    if name not in BY_NAME:
        raise ValueError(f"Not a format Foldocs reads: {name!r}")
    return BY_NAME[name]


def described() -> str:
    """Every format, as people know them: for a message about a file that is none of them."""
    labels = [f.label for f in FORMATS]
    return ", ".join(labels[:-1]) + " or " + labels[-1]


# For the formats' modules: what more than one of them reads the same way.


def take_titles(body: Tag) -> tuple[str | None, str | None]:
    """The first paragraphs styled Title and Subtitle (class "title", "subtitle"), taken out of
    the document: they are its title and subtitle, not its text."""
    from ..text import plain

    found = []
    for cls in ("title", "subtitle"):
        p = body.find("p", class_=cls)
        found.append(plain(p.extract()) if p else None)
    return found[0], found[1]


def inline_image(src: str) -> bytes:
    """An image inside the document, as a data: URI; anything else raises ValueError."""
    if not src.startswith("data:") or "," not in src:
        raise ValueError(f"Not an image inside the document: {src[:80]}")
    return base64.b64decode(src.split(",", 1)[1])
