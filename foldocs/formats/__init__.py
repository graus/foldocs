"""The kinds of file a site is made from, whichever source they come from: what each one is, how
to recognize it, and how to read a document of that kind.

There are two kinds. A document (a Google Doc, a Word or a Markdown file) is a page of text; an
image is a photo. A document's format reads the file into clean HTML (the tags convert.py lists,
nothing else: no styles, no classes) with its title and subtitle, and says where its images come
from. That is all that sets one format apart: convert.convert() then makes every document a page
the same way, so a heading or a table looks the same on the site whatever it was written in.

Adding a format is a Document below, with the types and extensions it is known by, a read() and a
line in FORMATS; the reading itself goes in a module of its own in this folder (gdoc.py, word.py,
markdown.py), with a read(file), imported only when a file of that format is read. Nothing else
names a format: the sources list what format_for() recognizes. tests/test_formats.py checks every
format against this contract.
"""

from __future__ import annotations

import base64
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import quote, unquote, urlparse

if TYPE_CHECKING:
    from bs4 import Tag

    from ..sources import Entry, Source

DOC = "application/vnd.google-apps.document"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MARKDOWN = frozenset({"text/markdown", "text/x-markdown"})
# The images published: photos, screenshots and GIFs. Others (SVG, PSD, camera RAW) are left out.
IMAGE_TYPES = frozenset(f"image/{t}" for t in ("jpeg", "png", "webp", "gif", "heic", "heif"))
# What a server says of a file it doesn't know the type of: then its extension says.
UNTYPED = frozenset({"", "text/plain", "application/octet-stream"})


Tree = dict[tuple[str, ...], "Entry"]  # every file in the folder, by its folders' names and its own


@dataclass
class Reading:
    """A document being read: the file, the source it comes from, and every file in the folder by
    its folders' names and its own (hidden ones too), for a document that shows another file."""

    entry: Entry
    source: Source
    tree: Tree = field(default_factory=dict)
    # Every path it looked up in the folder, and what it found there (see found()): the page is
    # redone when that changes, a file there replaced, changed, gone or there at last.
    uses: dict[tuple[str, ...], list[str] | None] = field(default_factory=dict)

    def download(self, other: Entry | None = None) -> bytes:
        """The file itself, or another file in the folder it shows."""
        return self.source.download(other or self.entry)

    def linked(self, src: str) -> Entry | None:
        """The file a link in the document leads to by a path from the document's folder
        (photo.jpg, ../photos/photo.jpg, .attachments.12/photo.jpg); None if src is no such path,
        one that leads out of the folder, or one with no file (yet). Recorded in `uses`."""
        path = self._path(src)
        if path is None:
            return None
        self.uses[path] = found(self.tree, path)
        return self.tree.get(path)

    def page_link(self, href: str) -> str | None:
        """A link in the document to another document, photo or folder in the folder, by a path
        from the document's folder (Notes.md, ../Trip/, Plan.docx#day-one) or by the service's own
        link to the file (a Google Doc's): as a folder: address, which the build makes the
        address of that page, by the file's ID where it is known (it may be renamed), else by its
        path (it may be added later). None for a link to anything else."""
        from ..sources import Links

        fragment = urlparse(href).fragment
        if (path := self._path(href)) is not None:
            entry = self.tree.get(path)
        elif isinstance(self.source, Links) and (file_id := self.source.file_id(href)):
            # A place in a Doc it links to is no place on its page; a file not in the folder (yet)
            # is known by its ID alone, and the build finds it if it is there by then.
            entry, fragment = self._by_id.get(file_id), ""
            path = entry.where if entry else ()
            if entry is None:
                return f"folder:/?id={quote(file_id, safe='')}"
        else:
            return None
        address = "folder:/" + "/".join(quote(name, safe="") for name in path)
        return address + (f"?id={entry.id}" if entry else "") + (f"#{fragment}" if fragment else "")

    @cached_property
    def _by_id(self) -> dict[str, Entry]:
        return {e.id: e for e in self.tree.values()}

    def _path(self, src: str) -> tuple[str, ...] | None:
        """The path in the folder a link leads to from the document's folder, or None if it is no
        path from there, or one that leads out of the folder."""
        parts = urlparse(src)
        if parts.scheme or parts.netloc or not parts.path or src.startswith(("/", "#")):
            return None
        path = list(self.entry.path)
        for name in map(unquote, parts.path.split("/")):  # a name can have a / in it (%2F), in Drive
            if name == "..":
                if not path:
                    return None
                path.pop()
            elif name not in ("", "."):
                path.append(name)
        return tuple(path)

    def used(self) -> list[list]:
        """`uses`, as a document's content records it: [path, found] pairs."""
        return [[list(path), then] for path, then in self.uses.items()]


def found(tree: Tree, path: list[str] | tuple[str, ...]) -> list[str] | None:
    """The file at a path in the folder (["writing", "photo.jpg"]), as a document's content records
    it: its ID and modification time; None if there is none."""
    entry = tree.get(tuple(path))
    return [entry.id, entry.modified] if entry else None


@dataclass
class DocText:
    """A document as its format reads it."""

    body: Tag  # its clean HTML, parsed: for convert(), which takes it apart
    image: Callable[[str], bytes]  # an <img> src in it, as the image; raises if it can't be had
    title: str | None = None  # where the document names one (the Title style); else convert looks for it
    subtitle: str | None = None


class Format(ABC):
    """A kind of file the site is made from."""

    name: ClassVar[str]  # short, as an entry records it: "gdoc", "word", "markdown", "image"
    label: ClassVar[str]  # as people know it, for messages: "a Word file"
    mimes: ClassVar[frozenset[str]]  # the types a source lists it as
    extensions: ClassVar[tuple[str, ...]] = ()  # lower case; left out of its title, and say it when the type doesn't

    @property
    def kind(self) -> str:
        """ "doc" (a page of text) or "image" (a photo)."""
        return "doc" if isinstance(self, Document) else "image"

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

        return word.read(file)


class MarkdownFile(Document):
    """A Markdown file (.md), as Nextcloud's Text app writes them; its images are other files in
    the folder."""

    name, label = "markdown", "a Markdown file"
    mimes = MARKDOWN
    extensions = (".md", ".markdown")

    def read(self, file: Reading) -> DocText:
        from . import markdown

        return markdown.read(file)


class Image(Format):
    """A photo, a screenshot or a GIF: a page of its own, or a portrait."""

    name, label = "image", "an image"
    mimes = IMAGE_TYPES


# Every format, in the order they are tried.
FORMATS: tuple[Format, ...] = (GoogleDoc(), WordFile(), MarkdownFile(), Image())


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


SEPARATOR = re.compile(r"[\s·•‧|–—,]*")  # what goes between links in a row of them


def code_by_font_only(body: Tag) -> None:
    """Text a format made code for its monospace font, where the font is only its look: in a
    heading, as a link's text, or a separator alone (the dots between links). As text pasted from a
    web page comes, or a heading in a typewriter font: no file name or setting, which is what code is
    for. Code of other punctuation ({}, ->, a lone } in a listing) stays code."""
    for code in body.find_all("code"):
        heading = code.find_parent(re.compile(r"h[1-6]"))
        if heading or code.find("a") or code.find_parent("a") or SEPARATOR.fullmatch(code.get_text()):
            code.unwrap()


def inline_image(src: str) -> bytes:
    """An image inside the document, as a data: URI; anything else raises ValueError."""
    if not src.startswith("data:") or "," not in src:
        raise ValueError(f"Not an image inside the document: {src[:80]}")
    return base64.b64decode(src.split(",", 1)[1])
