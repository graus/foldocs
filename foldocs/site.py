"""The site as a model, from the synced cache: collect() turns it into a Site of sections,
folders, items and pages, each with its address. render.py writes it out with a theme.

Files in the root folder are the home page's opening and the site's own pages. Every folder in it
is a section: a row on the home page, a page and a menu item. Its subfolders are categories within
it. An intro Doc opens its folder's page: its title names the folder, its subtitle describes it,
and its body comes first. A number opening a folder's name sets its order. A folder whose files
are all numbered is ordered by those numbers; any other folder shows its newest files first.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo

from . import contact
from .cache import Cache
from .config import Settings, role
from .sources import Entry, Folder, Source, open_source
from .text import display_name, excerpt, photo_title, slugify, split_date, split_number
from .theme import Theme

log = logging.getLogger(__name__)

# Addresses that are never a folder's or a page's: the site's images and the theme's files.
RESERVED = ("/media/", "/static/")


@dataclass
class Category:
    """A folder: a section of the site (top level) or a category within one."""

    name: str  # as shown: the intro Doc's title, else the folder's name without a number prefix
    url: str
    folder_name: str = ""  # as in the folder, number and all: for sorting
    description: str = ""  # the intro Doc's subtitle
    intro: str = ""  # the intro Doc's body, for the top of the folder's page
    intro_text: str = ""  # its paragraphs as plain text, for search results
    image: dict | None = None  # the intro Doc's first image: the folder's picture in link previews
    source_url: str = ""  # the folder at its source (Drive, Nextcloud), for a theme that links to it
    parent: "Category | None" = None
    children: list["Category"] = field(default_factory=list)
    items: list["Item"] = field(default_factory=list)  # its own and its subfolders', in order
    ordered: bool = False  # its files are all numbered, and shown in that order

    @property
    def trail(self) -> list["Category"]:
        """Its ancestors from the section down, then itself."""
        trail, cat = [], self
        while cat.parent:
            trail.insert(0, cat)
            cat = cat.parent
        return trail

    @property
    def section(self) -> "Category":
        return self.trail[0]


@dataclass
class Item:
    id: str
    kind: str  # "post", "photo" or "page" (from the root folder)
    title: str | None
    date: datetime
    url: str = ""
    label: str = ""  # a page's menu label: its file name
    heading: str = ""  # the title, or for an untitled photo its date
    number: int | None = None  # from a number opening the file name
    updated: datetime | None = None  # only when edited on a later day than it went into the folder
    html: str = ""
    subtitle: str | None = None
    excerpt: str = ""
    image: dict | None = None  # the photo, or a document's first image
    caption: str = ""
    source_url: str = ""  # the file at its source (Drive, Google Docs, Nextcloud), for a theme that links to it
    category: Category | None = None  # None for a page in the root folder
    newer: "Item | None" = None  # its neighbors within its section, in the section's order
    older: "Item | None" = None

    @property
    def folders(self) -> list[tuple[str, str]]:
        """The folders above it, from its section down, with their page: its breadcrumbs."""
        return [(c.name, c.url) for c in self.category.trail] if self.category else []


@dataclass
class Hero:
    """The home page's opening, from the root folder's intro Doc."""

    headline: str  # its title, else the author's name
    label: str | None  # its subtitle
    intro: str | None  # its first paragraph
    list_title: str | None  # its first heading
    items: list[str]  # its first list


@dataclass
class Site:
    hero: Hero
    pages: list[Item]  # the Docs in the root folder, in their order
    portrait: dict | None
    portrait_label: str  # the portrait's description, where the source has them
    email: str | None  # the first email link in the root folder's intro
    profiles: list[tuple[str, str]]  # its links to profiles elsewhere, as (site, url)
    sections: list[Category]  # the top-level folders, in order
    categories: list[Category]  # every folder in a section, the sections included
    items: list[Item]  # everything in the sections, newest first
    design: dict = field(default_factory=dict)  # the design Doc's settings (see design.py)
    font_faces: str = ""  # the @font-face rules for the fonts it names
    # Where a link from a document to another file leads, by the file's ID and by its path (as a
    # tuple of names), and to a folder, by its path: the page's address
    destinations: dict[str | tuple[str, ...], str] = field(default_factory=dict)


class Addresses:
    """Hands out page addresses, each once: a taken one gets -2, -3 and so on."""

    def __init__(self, reserved) -> None:
        self.taken = set(reserved)

    def claim(self, parent: str, slug: str) -> str:
        url, n = f"{parent}{slug}/", 2
        while url in self.taken:
            url, n = f"{parent}{slug}-{n}/", n + 1
        self.taken.add(url)
        return url


@dataclass
class _Root:
    """What the root folder holds besides its sections."""

    intro: dict | None = None
    design: dict | None = None
    portrait: dict | None = None
    portrait_label: str = ""
    pages: list[Item] = field(default_factory=list)


class NothingSynced(Exception):
    pass


def collect(settings: Settings, theme: Theme, source: Source | None = None) -> Site:
    cache = Cache(settings.cache_dir)
    manifest = cache.manifest()
    if manifest is None:
        raise NothingSynced("Nothing synced yet: run `foldocs sync` first")
    source = source or open_source(settings)
    tz = ZoneInfo(settings.timezone)
    folder_urls = {tuple(f["path"]): source.folder_url(Folder(f["path"], f.get("id", ""))) for f in cache.folders()}
    # The theme's addresses are taken, so a folder or page with the same name gets another one.
    addresses = Addresses([*RESERVED, *(extra.url for extra in theme.extras.values())])
    root, intros, shown = _Root(), {}, []
    tree = _Tree(addresses, folder_urls)

    # Oldest first, so an existing item keeps its address when a newer one gets the same title;
    # and where one Doc has a role, the newest is the one that counts.
    for raw in sorted(manifest, key=lambda e: e["created"]):
        entry = Entry(**raw)
        if not (content := cache.content(entry.id)):
            continue
        shown.append(entry)
        if not entry.path:
            _add_to_root(root, entry, content, source, tz)
        elif role(entry) == "intro":
            _newest(intros, tuple(entry.path), content, entry)
        else:
            tree.add(_item(entry, content, source, tz), entry.path)

    # Pages get their addresses after the sections, so a page never moves a section: a page
    # "Work" next to "1 Work" is at /work-2/. Numbered pages first, then the others by name.
    for page in sorted(root.pages, key=lambda i: i.date):
        page.url = addresses.claim("/", slugify(page.label))
    root.pages.sort(key=lambda i: (i.number is None, i.number or 0, i.label.lower()))
    tree.apply(intros)
    for item in [*root.pages, *tree.items]:
        item.heading = item.title or theme.date(item.date)
    tree.order()

    email, profiles = contact.links(root.intro.get("links", [])) if root.intro else (None, [])
    return Site(
        hero=_hero(root.intro, settings.author),
        pages=root.pages,
        portrait=root.portrait,
        portrait_label=root.portrait_label,
        email=email,
        profiles=profiles,
        sections=tree.root.children,
        categories=tree.folders,
        items=sorted(tree.items, key=lambda i: i.date, reverse=True),
        design=(root.design or {}).get("design", {}),
        font_faces=(root.design or {}).get("fonts", ""),
        destinations=_destinations(shown, [*root.pages, *tree.items], tree),
    )


def _destinations(entries: list[Entry], items: list[Item], tree: "_Tree") -> dict:
    """Where a link to each file leads, by its ID and its path, and to each folder: a document or
    photo to its page, an intro to its folder's page (the root folder's to the home page)."""
    pages = {item.id: item.url for item in items}
    found: dict[str | tuple[str, ...], str] = {path: cat.url for path, cat in tree.categories.items()}
    for entry in entries:
        url = pages.get(entry.id) or (found.get(tuple(entry.path)) if role(entry) == "intro" else None)
        if url:
            found[entry.id] = found[entry.where] = url
    return found


# The root folder ---------------------------------------------------------------------------------


def _add_to_root(root: _Root, entry: Entry, content: dict, source: Source, tz: tzinfo) -> None:
    """A file in the root folder: the portrait, the home page's intro, the design Doc, or a page.
    Each comes oldest first: the newest photo is the portrait, so a new one replaces the old."""
    if entry.kind == "image":
        root.portrait, root.portrait_label = content["image"], entry.description.strip()
    elif role(entry) == "intro":
        _warn_if_second(root.intro, entry)
        root.intro = content
    elif role(entry) == "design":
        _warn_if_second(root.design, entry)
        root.design = content
    else:  # a page, in the menu; its address and label from its name, like a folder's
        page = _item(entry, content, source, tz)
        page.kind, page.label = "page", display_name(entry.stem)
        root.pages.append(page)


def _newest(found: dict, key: tuple, content: dict, entry: Entry) -> None:
    _warn_if_second(found.get(key), entry)
    found[key] = content


def _warn_if_second(previous: dict | None, entry: Entry) -> None:
    if previous is not None:
        where = "/".join(entry.path) or "the root folder"
        log.warning("More than one %s Doc in %s; the newest, %s, is the one used", role(entry), where, entry.name)


def _hero(intro: dict | None, author: str) -> Hero:
    if not intro:
        return Hero(headline=author, label=None, intro=None, list_title=None, items=[])
    first = intro["first"]
    return Hero(
        headline=intro["title"] or author,
        label=intro["subtitle"],
        intro=first["paragraph"],
        list_title=first["heading"],
        items=first["list"],
    )


# The folders -------------------------------------------------------------------------------------


class _Tree:
    """The sections and their folders, as their files are added."""

    def __init__(self, addresses: Addresses, folder_urls: dict[tuple, str]) -> None:
        self.addresses, self.folder_urls = addresses, folder_urls
        self.root = Category("", "/")
        self.categories: dict[tuple[str, ...], Category] = {(): self.root}
        self.items: list[Item] = []

    @property
    def folders(self) -> list[Category]:
        return [c for c in self.categories.values() if c is not self.root]

    def add(self, item: Item, path: list[str]) -> None:
        """An item in its folder, which is made (and the folders above it) if it is the first."""
        cat = self.root
        for depth in range(1, len(path) + 1):
            key = tuple(path[:depth])
            if key not in self.categories:
                self.categories[key] = self._folder(cat, key)
            cat = self.categories[key]
        item.category = cat
        slug = slugify(item.title) if item.title else f"photo-{item.date:%Y%m%d-%H%M}"
        item.url = self.addresses.claim(cat.url, slug)
        self.items.append(item)
        while cat is not self.root:
            cat.items.append(item)
            cat = cat.parent

    def _folder(self, parent: Category, key: tuple[str, ...]) -> Category:
        name = display_name(key[-1])
        url = self.addresses.claim(parent.url, slugify(name))
        self.addresses.taken.add(f"{url}page/")  # its pages of cards: no subfolder or file takes it
        folder = Category(name, url, folder_name=key[-1], source_url=self.folder_urls.get(key, ""), parent=parent)
        parent.children.append(folder)
        return folder

    def apply(self, intros: dict[tuple, dict]) -> None:
        """Each folder's intro Doc: after the addresses, which come from the folder's own name."""
        for key, content in intros.items():
            if not (cat := self.categories.get(key)):
                log.info("Skipping the intro in %s: the folder has nothing else to show yet", "/".join(key))
                continue
            cat.name = content["title"] or cat.name
            cat.description = content["subtitle"] or ""
            cat.intro, cat.intro_text = content["html"], content["text"]
            cat.image = content["cover"]

    def order(self) -> None:
        """Every folder in its order, and each item's neighbors within its section."""
        for cat in self.categories.values():
            cat.ordered = bool(cat.items) and all(item.number is not None for item in cat.items)
            if cat.ordered:
                cat.items.sort(key=lambda i: (i.number, i.heading.lower()))
            else:
                cat.items.sort(key=lambda i: i.date, reverse=True)
            cat.children.sort(key=lambda c: c.folder_name.lower())
        for section in self.root.children:
            for newer, older in zip(section.items, section.items[1:], strict=False):
                newer.older, older.newer = older, newer


# The files ---------------------------------------------------------------------------------------


def _item(entry: Entry, content: dict, source: Source, tz: tzinfo) -> Item:
    named = _named_date(entry.stem, tz)
    number = split_number(split_date(entry.stem)[1])[0]
    if entry.kind == "doc":
        uploaded, modified = _source_time(entry.created, tz), _source_time(entry.modified, tz)
        return Item(
            id=entry.id,
            kind="post",
            title=content["title"] or display_name(entry.stem),
            date=named or uploaded,
            number=number,
            # Edited on a later day than it went into the folder, and after the date it shows
            updated=modified if modified.date() > max(uploaded, named or uploaded).date() else None,
            html=content["html"],
            subtitle=content["subtitle"],
            excerpt=content["subtitle"] or excerpt(content["text"]),
            image=content["cover"],
            source_url=source.file_url(entry),
        )
    image = content["image"]
    taken = _exif_time(entry.taken, tz) or _exif_time(image.get("taken"), tz)
    return Item(
        id=entry.id,
        kind="photo",
        title=photo_title(entry.name),
        date=named or taken or _source_time(entry.created, tz),
        number=number,
        image=image,
        caption=entry.description,
        excerpt=excerpt(entry.description),
        source_url=source.file_url(entry),
    )


def _source_time(value: str, tz: tzinfo) -> datetime:
    return datetime.fromisoformat(value).astimezone(tz)


def _exif_time(value: str | None, tz: tzinfo) -> datetime | None:
    """Cameras record local time without a zone; assume it was taken in the site's."""
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value or "", fmt).replace(tzinfo=tz)
        except ValueError:
            pass
    return None


def _named_date(name: str, tz: tzinfo) -> datetime | None:
    """The date a file name opens with, at noon (so it stays that day in any time zone)."""
    ymd, _ = split_date(name)
    try:
        return datetime(*ymd, 12, tzinfo=tz) if ymd else None
    except ValueError:
        return None
