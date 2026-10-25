"""Render a Site (site.py) with a theme's templates, and write it to the public folder.

build() is the whole of it: the theme for this source, the site from the cache, the links in its
documents made local, the site's own design over the theme's, then every page, the 404 page, the
feed, the sitemap and robots.txt. Only files that changed are written.
"""

import hashlib
import logging
import os
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup, escape

from . import config, design, images, seo, themecheck
from . import theme as themes
from .config import Settings
from .site import Item, Site, collect
from .sources import Source, open_source
from .text import ucfirst
from .theme import Theme

log = logging.getLogger(__name__)

PER_PAGE = 24  # cards on a folder's page; more go on page 2, 3…
FEED_LENGTH = 20  # the newest items in feed.xml


@dataclass
class MenuItem:
    label: str
    url: str
    kind: str  # "section" or "page"
    children: list["MenuItem"] = field(default_factory=list)  # a section's subfolders, one level deep


def build(settings: Settings) -> None:
    source = open_source(settings)
    theme = _theme_for(settings, source)
    site = collect(settings, theme, source)
    _localize_links(site, settings, theme)
    static, theme = _static_files(settings, site, theme)
    nav = _navigation(site)
    env = _environment(settings, theme, source, static, site, nav)
    heads = seo.Pages(settings, theme, site, static)
    tz = ZoneInfo(settings.timezone)
    files: dict[str, str | bytes] = {f"static/{name}": data for name, data in static.items()}
    sitemap: dict[str, datetime | None] = {}  # every page's URL, with when it last changed

    def page(url: str, template: str, head: seo.Head, trail: list, lastmod=None, active=None, **context) -> None:
        """Render a page. `trail` is its breadcrumbs below Home as (label, url) pairs, ending with
        the page itself; `active` the menu item to mark, by default the one the URL is under."""
        if active is None:
            active = next((item.url for item in nav if url.startswith(item.url)), None)
        path = url.strip("/") + "/index.html" if url != "/" else "index.html"
        crumbs = [(theme.home, "/"), *trail] if trail else None
        files[path] = env.get_template(template).render(url=url, active=active, crumbs=crumbs, head=head, **context)
        sitemap[url] = lastmod

    page(
        "/",
        "home.html",
        heads.home(),
        [],
        _changed(site.items),
        hero=site.hero,
        portrait=site.portrait,
        portrait_label=site.portrait_label,
        sections=site.sections,
        items=site.items,  # everything, newest first: for a theme that shows one stream
    )
    for item in [*site.pages, *site.items]:
        crumbs = [*item.folders, (item.heading, None)]
        page(item.url, "item.html", heads.item(item, crumbs), crumbs, _changed([item]), item=item)
    for cat in site.categories:
        crumbs = [(c.name, c.url) for c in cat.trail[:-1]] + [(cat.name, None)]
        for url, pagination in _pages(cat.items, cat.url):
            head = heads.category(cat, url, pagination["page"], crumbs)
            page(url, "list.html", head, crumbs, _changed(cat.items), category=cat, **pagination)
    for extra in theme.extras.values():
        crumbs = [(extra.label, None)]
        page(extra.url, extra.template, heads.extra(extra, crumbs), crumbs, active="")

    files["404.html"] = env.get_template("404.html").render(
        url="/404.html", active="", crumbs=None, head=heads.not_found()
    )
    feed = site.items[:FEED_LENGTH]
    files["feed.xml"] = env.get_template("feed.xml").render(items=feed, updated=_changed(feed) or datetime.now(tz))
    files["sitemap.xml"] = env.get_template("sitemap.xml").render(urls=sitemap)
    # A site kept out of search results says so on every page (noindex); robots.txt lets crawlers
    # read the pages, or they would never see it, and offers no sitemap.
    sitemap_line = "" if env.globals["noindex"] else f"\nSitemap: {settings.site_url}/sitemap.xml\n"
    files["robots.txt"] = f"User-agent: *\nAllow: /\n{sitemap_line}"

    written = _write(settings.public_dir, files)
    log.info("Built %d pages and files (%d changed)", len(files), written)


def _theme_for(settings: Settings, source: Source) -> Theme:
    """The theme, without its pages written for another source (say, explaining a Drive folder)."""
    theme = themes.load(settings.theme_dir)
    extras = {key: extra for key, extra in theme.extras.items() if not extra.sources or source.kind in extra.sources}
    return replace(theme, extras=extras)


def _localize_links(site: Site, settings: Settings, theme: Theme) -> None:
    """Links to the site itself, written in full (a Google Doc keeps no other kind), made local,
    so they stay on whichever variant they are read; and links to other files in the folder, to
    their pages."""
    own = settings.original_url or settings.site_url
    pages = [*site.pages, *site.items]
    here = {"/", "/feed.xml", "/sitemap.xml"} | {c.url for c in site.categories} | {p.url for p in pages}
    here |= {extra.url for extra in theme.extras.values()}
    for item in pages:
        item.html = local_links(folder_links(item.html, site.destinations), own, here)
    for cat in site.categories:
        cat.intro = local_links(folder_links(cat.intro, site.destinations), own, here)


ICONS = ("favicon.svg", "apple-touch-icon.png")


def _static_files(settings: Settings, site: Site, theme: Theme) -> tuple[dict[str, bytes], Theme]:
    """The theme's static files, and the site's own design over the theme's, as design.css. Every
    file is cached for a year (its URL changes with it), so a stylesheet's url() to another one
    gets that file's version too: a changed font is a new URL, not a year-old copy."""
    static = theme.static_files()
    if settings.original_url:  # a variant (in the original theme's variants/) keeps the site's icons
        original = themes.load(settings.theme_dir.parent.parent).static_files()
        own = theme.dir / "static"
        static |= {name: original[name] for name in ICONS if name in original and not (own / name).is_file()}
    if css := design.stylesheet(site.design, site.font_faces):
        static["design.css"] = css.encode()
        theme = replace(theme, stylesheets=(*theme.stylesheets, "design.css"))
    versions = _versions(static)
    for name in [n for n in static if n.endswith(".css")]:
        folder = name.rpartition("/")[0]

        def versioned(m: re.Match, folder=folder) -> str:
            target = f"{folder}/{m[1]}".lstrip("/")
            return f"url({m[1]}?v={versions[target]})" if target in versions else m[0]

        static[name] = re.sub(r"url\(([\w./-]+)\)", versioned, static[name].decode()).encode()
    return static, theme


def _versions(static: dict[str, bytes]) -> dict[str, str]:
    """A short hash of each static file, for its URL: /static/style.css?v=1a2b3c4d."""
    return {name: hashlib.sha1(data).hexdigest()[:8] for name, data in static.items()}


def _backgrounds(theme: Theme, static: dict[str, bytes]) -> dict[str, str]:
    """The background color in light and in dark mode, where the stylesheets set it as hex."""
    light, dark = themecheck.tokens_in("\n".join(static[s].decode() for s in theme.stylesheets if s in static))
    colors = {mode: themecheck.resolve(values, "color-bg") for mode, values in (("light", light), ("dark", dark))
              if "color-bg" in values}  # fmt: skip
    return {mode: color for mode, color in colors.items() if themecheck.HEX.fullmatch(color)}


def _svg_size(svg: bytes) -> tuple[int, int] | None:
    """An SVG's width and height (or its viewBox's), for an <img> that takes no space until it loads."""
    head = svg[:2000].decode(errors="ignore")
    width, height = re.search(r'\swidth="([\d.]+)"', head), re.search(r'\sheight="([\d.]+)"', head)
    if width and height:
        return round(float(width[1])), round(float(height[1]))
    if box := re.search(r'viewBox="[\d.-]+[\s,]+[\d.-]+[\s,]+([\d.]+)[\s,]+([\d.]+)"', head):
        return round(float(box[1])), round(float(box[2]))
    return None


def _navigation(site: Site) -> list[MenuItem]:
    """The menu: each section with its subfolders, then each page in the root folder."""
    sections = [
        MenuItem(s.name, s.url, "section", [MenuItem(c.name, c.url, "section") for c in s.children])
        for s in site.sections
    ]
    return sections + [MenuItem(p.label, p.url, "page") for p in site.pages]


def _environment(
    settings: Settings, theme: Theme, source: Source, static: dict[str, bytes], site: Site, nav: list[MenuItem]
) -> Environment:
    """The templates, and everything every template can use."""
    env = Environment(
        loader=FileSystemLoader(theme.templates),
        autoescape=select_autoescape(["html", "xml"]),
        undefined=StrictUndefined,  # a typo in a template fails the build instead of rendering blank
        trim_blocks=True,
        lstrip_blocks=True,
    )
    versions = _versions(static)
    icon = lambda name: name if name in static else None  # noqa: E731
    env.globals |= {
        "site": {"name": settings.site_name, "url": settings.site_url, "author": settings.author},
        "theme": theme,
        # The theme's words by name, as plain attributes: `t.items` is a word, not dict.items.
        "t": SimpleNamespace(**theme.strings),
        # A line atop every page, from the theme's words, if it has one; a variant's always links back.
        "notice": theme.strings.get("notice", ""),
        "original_url": settings.original_url,
        # The variants of this site by name, with their address, for a theme to link to.
        "variants": {Path(v.output).name: v.site_url + "/" for v in config.variants(settings)},
        # The shared folder (public, like the site), for a theme that shows where the site comes from;
        # `embed` is "" for a source that can't be framed.
        "source": {"kind": source.kind, "url": source.url, "embed": source.embed},
        # A variant is a copy of the site to look at, never to find: it always asks search engines away.
        "noindex": settings.noindex or bool(settings.original_url),
        "nav": nav,
        "contact": {"email": site.email, "profiles": site.profiles},
        # The design Doc's settings, for a theme to adapt to (say, not preloading a font it replaced).
        "design": site.design,
        # Icons, when the theme has them: a favicon.svg for browsers, a 180px PNG for phone home screens.
        "favicon": icon("favicon.svg"),
        "touch_icon": icon("apple-touch-icon.png"),
        # A logo for the header, in place of the author's name; a dark-mode version if there is one.
        "logo": icon("logo.svg"),
        "logo_size": _svg_size(static["logo.svg"]) if "logo.svg" in static else None,
        # The page's background, light and dark: the browser's own bar takes it on a phone.
        "theme_colors": _backgrounds(theme, static),
        "logo_dark": icon("logo-dark.svg"),
        "picture": _picture,
        "asset": lambda name: f"/static/{name}?v={versions[name]}",
        "now": datetime.now(ZoneInfo(settings.timezone)),
    }
    env.filters |= {
        "date": theme.date,
        "iso": lambda d: d.isoformat(timespec="seconds"),
        "ucfirst": ucfirst,
        "signal": _signal,
        # Root-relative URLs in content (links, images, srcset entries) made absolute, for the feed.
        "absolute": lambda html: re.sub(
            r'(?<=href=")/(?!/)|(?<=src=")/(?!/)|(?<=[\s,"])/(?=media/|static/)', settings.site_url + "/", str(html)
        ),
    }
    return env


def folder_links(html: str, destinations: dict) -> str:
    """A page's links to other files and folders in the folder (folder: addresses, see
    formats.Reading.page_link) as links to their pages: by the file's ID, else by its path. A link
    to a file with no page (a hidden one, or one gone) is its text alone."""
    if 'href="folder:' not in html:
        return html
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=re.compile("^folder:")):
        parts = urlsplit(a["href"])
        path = tuple(unquote(name) for name in parts.path.strip("/").split("/") if name)
        url = destinations.get(parse_qs(parts.query).get("id", [""])[0]) or destinations.get(path)
        if url:
            a["href"] = url + (f"#{parts.fragment}" if parts.fragment else "")
        elif a.get("id"):
            del a["href"]
        else:
            a.unwrap()
    return str(soup)


def local_links(html: str, site_url: str, pages: set[str] | None = None) -> str:
    """Links to pages of this site (its address, with or without www and https) as local ones;
    with `pages`, only to those (a variant may lack a page the original has: that link stays full)."""
    host = re.escape(urlparse(site_url).hostname or "")

    def local(m: re.Match) -> str:
        path = "/" + m[1]
        if pages is not None and urlparse(path).path not in pages:
            return m[0]
        return f'href="{path}'

    return re.sub(rf'href="https?://(?:www\.)?{host}(?=[/"#?])/?([^"]*)', local, html)


def _signal(text: str) -> Markup:
    """A headline with its last word marked, for the theme to set off."""
    words = text.strip().rsplit(" ", 1)
    head = escape(words[0]) + " " if len(words) == 2 else ""
    return Markup(f'{head}<span class="signal">{escape(words[-1])}</span>')


def _picture(info: dict, sizes: str = images.SIZES, alt: str = "", lazy: bool = True, priority: bool = False) -> Markup:
    """An <img> of a processed image: every variant in srcset, its size, its color while loading.
    Lazy, unless it shows at the top of the page; `priority` for the page's main image (its LCP),
    which the browser then fetches first."""
    attrs = " ".join(f'{name}="{escape(value)}"' for name, value in images.attrs(info, sizes).items())
    if priority:
        return Markup(f'<img {attrs} alt="{escape(alt)}" fetchpriority="high">')
    loading = ' loading="lazy"' if lazy else ""
    return Markup(f'<img {attrs} alt="{escape(alt)}"{loading} decoding="async">')


def _pages(items: list[Item], url: str):
    """The pages of a list: (url, context) for each run of PER_PAGE items."""
    chunks = [items[i : i + PER_PAGE] for i in range(0, len(items), PER_PAGE)] or [[]]
    urls = [url if n == 1 else f"{url}page/{n}/" for n in range(1, len(chunks) + 1)]
    for n, chunk in enumerate(chunks):
        yield (
            urls[n],
            {
                "items": chunk,
                "page": n + 1,
                "page_count": len(chunks),
                "newer_url": urls[n - 1] if n > 0 else None,
                "older_url": urls[n + 1] if n + 1 < len(urls) else None,
            },
        )


def _changed(items: list[Item]) -> datetime | None:
    """When the newest of these items last changed: a page's date for the sitemap and feed."""
    return max((i.updated or i.date for i in items), default=None)


def _write(public: Path, files: dict[str, str | bytes]) -> int:
    """Write what changed (each file atomically), and remove pages that are gone.
    The media folder belongs to the sync and is left alone."""
    written = 0
    for rel, content in files.items():
        data = content.encode() if isinstance(content, str) else content
        path = public / rel
        if path.exists() and path.read_bytes() == data:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        written += 1

    for path in sorted(public.rglob("*"), reverse=True):  # children before their folders
        rel = path.relative_to(public).as_posix()
        if rel == "media" or rel.startswith("media/"):
            continue
        if path.is_file() and rel not in files:
            path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    return written
