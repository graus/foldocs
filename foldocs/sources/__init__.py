"""Where the site's folder lives: the files a source lists, and what every source can do.

A source is read-only and works from a public share: Google Drive (drive.py) or a Nextcloud
share link (nextcloud.py). It lists the tree, hands over a file's bytes, and says where a file or
folder can be opened. Everything after that (the cache, the conversion, the site) is the same
whichever source the files come from.

A service (Google Drive, Nextcloud) is where the folder lives; its Provider says how to recognize
its links and open them, and gives its short name (kind: "drive", "nextcloud"); the Source it opens
reads one folder.

What a file is (a Doc, a Word or Markdown file, an image) is not a source's business: the formats
are in formats/, and a source lists whatever format_for() recognizes, with the format it found. A
service with a document format of its own (Google Docs) can export it: Exports below.

Adding a service is one module in this folder: a class with the methods of Source below, and a PROVIDER that says how to
recognize its links, what settings it needs and how to open it; then a line in providers().
It reads every answer with body() and counts every listing with counted(), so the limits a host
of strangers' folders sets (max_bytes, max_items) hold for it too. Nothing else names a service.
tests/test_sources.py checks every service against this contract.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import requests

from .. import formats

TIMEOUT = 120  # seconds, for one request to a service
DOWNLOAD_MAX = 50 << 20  # bytes: a larger file is refused rather than let it take the sync's memory


class TooLarge(ValueError):
    """A folder or a file past a limit its source was given (max_items, max_bytes)."""


def body(resp: requests.Response, limit: int | None = None) -> bytes:
    """A streamed response's body, up to `limit` bytes (DOWNLOAD_MAX if None), counted as it unpacks:
    a small compressed answer can't grow into a huge one. An error status raises requests.HTTPError,
    a longer body TooLarge. The response is closed either way."""
    limit = DOWNLOAD_MAX if limit is None else limit
    data = bytearray()
    with resp:
        resp.raise_for_status()
        for chunk in resp.iter_content(1 << 16):
            data += chunk
            if len(data) > limit:
                size = f"{limit >> 20} MB" if limit >= 1 << 20 else f"{limit:,} bytes"
                raise TooLarge(f"over {size}: too large to publish")
    return bytes(data)


def counted(source, children: list) -> list:
    """A folder's children, counted towards the source's max_items; past it, TooLarge."""
    source.listed += len(children)
    if source.max_items is not None and source.listed > source.max_items:
        raise TooLarge(f"more than {source.max_items} files and folders")
    return children


@dataclass
class Entry:
    """A publishable file in the tree: a document or an image. What every source promises:"""

    id: str  # stable across renames and moves where the service allows; it names a cache file, so
    # letters, digits, "-" and "_" only (hash anything else a server sends)
    name: str
    mime: str  # as the service gives it
    path: list[str]  # names of the folders between the root and the file
    created: str  # RFC 3339 in UTC, as "2026-10-23T08:00:00Z" (Drive adds milliseconds): orders the files
    modified: str  # the same; changes whenever the content does (the cache goes by it)
    description: str = ""  # a caption, where the service has them
    taken: str | None = None  # EXIF capture time as "YYYY:MM:DD HH:MM:SS", where the service has it
    format: str = ""  # its format's name, as the source found it with formats.format_for()

    def __post_init__(self) -> None:
        if not self.format:  # an entry made without one (in a test, say): found from the type and name
            found = formats.format_for(self.mime, self.name)
            if found is None:
                raise ValueError(f"{self.name}: not a file the site publishes ({self.mime})")
            self.format = found.name

    @property
    def reader(self) -> formats.Format:
        """Its format: what it is, and how it is read."""
        return formats.named(self.format)

    @property
    def kind(self) -> str:
        """ "doc" or "image"."""
        return self.reader.kind

    @property
    def stem(self) -> str:
        """The name without its extension (.docx, .md), for titles and roles."""
        return self.reader.stem(self.name)

    @property
    def where(self) -> tuple[str, ...]:
        """Its place in the folder: the names of the folders it is in, and its own."""
        return (*self.path, self.name)

    @property
    def hidden(self) -> bool:
        return hidden(self.where)


@dataclass
class Folder:
    """A folder in the tree."""

    path: list[str]  # names of the folders from the root down to this one
    id: str = ""

    @property
    def hidden(self) -> bool:
        return hidden(self.path)


def hidden(path: list[str] | tuple[str, ...]) -> bool:
    """Whether a file or folder is hidden, or in a hidden folder: a name starting with a dot. It is
    not on the site, but there for a document that shows it, as Nextcloud keeps a Markdown file's
    images."""
    return any(name.startswith(".") for name in path)


@dataclass
class DocExport:
    html: str
    files: dict[str, bytes] = field(default_factory=dict)  # images bundled with the export, by src


class Source(Protocol):
    """A shared folder, read only. Making one sends no request: building the site needs no network."""

    kind: str  # the Provider's: for a theme page written for one service
    url: str  # the shared folder, to open in a browser
    embed: str  # the folder as a page to show in an iframe, or "" if the service has none
    # Limits for a folder of a stranger's: a site's own folder has none but DOWNLOAD_MAX.
    max_items: int | None  # files and folders walk() lists at most; past it, TooLarge
    max_bytes: int | None  # bytes in one answer from the service (a listing, a file); None: DOWNLOAD_MAX

    def walk(self) -> tuple[list[Entry], list[Folder]]:
        """All publishable files under the root, and the folders they are in. Raises, rather
        than returning nothing, if the root can't be read: an empty tree would empty the site."""
        ...

    def download(self, entry: Entry) -> bytes:
        """The file itself: an image, or a document that is a file (Word, Markdown)."""
        ...

    def file_url(self, entry: Entry) -> str:
        """Where to open the file, for a link from its page."""
        ...

    def folder_url(self, folder: Folder) -> str:
        """Where to open the folder, or "" if unknown."""
        ...


@runtime_checkable
class Exports(Protocol):
    """A source whose service has a document format of its own (Google Docs), which it exports."""

    def export_doc(self, entry: Entry) -> DocExport:
        """A document in the service's own format as HTML."""
        ...

    def fetch(self, url: str) -> bytes:
        """An image an exported document links to rather than bundles; only from the service's own hosts."""
        ...


@runtime_checkable
class Links(Protocol):
    """A source whose service has links of its own to its files (a Google Doc's address), which a
    document in the folder can carry."""

    def file_id(self, url: str) -> str | None:
        """The ID of the file a link leads to, if it is a link to one of the service's files."""
        ...


@dataclass(frozen=True)
class Provider:
    """A service a shared folder can live in."""

    kind: str  # its name in a theme's `sources`: "drive", "nextcloud"
    label: str  # as people know it: "Google Drive"
    matches: Callable[[str], bool]  # whether FOLDER_URL is a link to it
    check: Callable[[str], object]  # raises ValueError, saying why, if the link is not a usable one
    open: Callable[..., Source]  # the source, from the settings
    keys: tuple[str, ...] = ()  # the settings it needs besides FOLDER_URL, such as an API key
    password: bool = False  # whether its share links can have a password (FOLDER_PASSWORD)


def providers() -> list[Provider]:
    """Every service, the most particular first: a Nextcloud server can be on any host."""
    from . import drive, nextcloud

    return [drive.PROVIDER, nextcloud.PROVIDER]


def provider_for(folder_url: str) -> Provider:
    found = next((p for p in providers() if p.matches(folder_url)), None)
    if not found:
        names = ", ".join(p.label for p in providers())
        raise ValueError(f"Not a share link from a service Foldocs reads ({names}): {folder_url}")
    return found


def open_source(settings) -> Source:
    """The source the settings name. Makes no request: building the site needs no network."""
    return provider_for(settings.folder_url).open(settings)
