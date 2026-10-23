"""What search engines and link previews read: a page's title, description, image and
structured data. The engine works it out for every page, so no theme has to get it right.

Each page gets a `head` for the theme's layout: plain values for the <title>, the description
and the Open Graph tags, and `ld`, the page as schema.org JSON-LD. That is one graph: the site
(WebSite), its author (Person), the page itself (a BlogPosting, Article, CollectionPage or
WebPage) and its breadcrumbs. Search engines use it for rich results and to know whose site it is.
"""

import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import datetime

from markupsafe import Markup
from PIL import Image

from .config import Settings
from .text import excerpt, ucfirst
from .theme import Theme

SHARE_IMAGE = "share.png"  # a theme's link preview image, in its static folder
DESCRIPTION_LENGTH = 160  # what a search result shows
HEADLINE_LENGTH = 110  # Google's limit for an article's headline
OG_IMAGE_WIDTH = 1600  # the largest variant a link preview needs


@dataclass
class Head:
    title: str
    description: str
    url: str  # absolute
    type: str = "website"  # Open Graph: website or article
    image: dict | None = None  # url, width, height, alt
    published: datetime | None = None
    modified: datetime | None = None
    noindex: bool = False
    ld: Markup = field(default_factory=lambda: Markup(""))


class Pages:
    """Builds the Head of each page of one site."""

    def __init__(self, settings: Settings, theme: Theme, site, static: dict[str, bytes]) -> None:
        self.settings, self.theme, self.site = settings, theme, site
        self.base = settings.site_url
        self.words = theme.strings
        self.card = self._card(static)
        self.person = {
            "@type": "Person",
            "@id": f"{self.base}/#person",
            "name": settings.author,
            "url": f"{self.base}/",
            **({"jobTitle": site.hero.label} if site.hero.label else {}),
            **({"description": site.hero.intro} if site.hero.intro else {}),
            **({"image": self.image(site.portrait)["url"]} if site.portrait else {}),
            **({"sameAs": [url for _, url in site.profiles]} if site.profiles else {}),
        }
        self.website = {
            "@type": "WebSite",
            "@id": f"{self.base}/#website",
            "url": f"{self.base}/",
            "name": settings.site_name,
            "inLanguage": theme.language,
            "publisher": {"@id": self.person["@id"]},
        }

    def _card(self, static: dict[str, bytes]) -> dict | None:
        """The theme's share image (static/share.png, ideally 1200×630): the link preview of a page
        without an image of its own."""
        if SHARE_IMAGE not in static:
            return None
        width, height = Image.open(io.BytesIO(static[SHARE_IMAGE])).size
        version = hashlib.sha1(static[SHARE_IMAGE]).hexdigest()[:8]
        alt = self.site.hero.headline or self.settings.site_name
        return {"url": f"{self.base}/static/{SHARE_IMAGE}?v={version}", "width": width, "height": height, "alt": alt}

    def fallback(self) -> dict | None:
        """The preview of a page without an image: the share image, else the portrait."""
        portrait = f"{self.words['portrait_of']} {self.settings.author}"
        return self.card or self.image(self.site.portrait, portrait)

    def image(self, info: dict | None, alt: str = "") -> dict | None:
        """The image a link preview shows: its largest variant up to OG_IMAGE_WIDTH."""
        if not info:
            return None
        fitting = [v for v in info["variants"] if v["w"] <= OG_IMAGE_WIDTH] or info["variants"][:1]
        variant = fitting[-1]
        return {
            "url": self.base + info["base"] + variant["file"],
            "width": variant["w"],
            "height": round(info["height"] * variant["w"] / info["width"]),
            "alt": alt,
        }

    def _head(self, url: str, title: str, description: str, node: dict, crumbs=None, **fields) -> Head:
        """A Head with its JSON-LD graph: the site, the author, this page and its breadcrumbs."""
        absolute = self.base + url
        node = {"@id": absolute, "url": absolute, "isPartOf": {"@id": self.website["@id"]}, **node}
        graph = [self.website, self.person, node]
        if crumbs:  # below Home, which comes first
            graph.append(self._breadcrumbs([(self.theme.home, "/"), *crumbs], absolute))
        data = json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False, default=str)
        return Head(
            title=title,
            description=excerpt(description, DESCRIPTION_LENGTH),
            url=absolute,
            # Not a <, > or & as is: nothing in a title or a name can close the <script>, start a
            # comment in it, or look like a tag to whatever reads the page as text.
            ld=Markup(data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")),
            **fields,
        )

    def _breadcrumbs(self, crumbs: list[tuple[str, str | None]], here: str) -> dict:
        """The trail to this page; a folder without a page of its own is left out."""
        steps = [(label, self.base + href) for label, href in crumbs[:-1] if href] + [(crumbs[-1][0], here)]
        return {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": n, "name": label, "item": url}
                for n, (label, url) in enumerate(steps, 1)
            ],
        }

    def _titled(self, *parts: str) -> str:
        return " · ".join([*parts, self.settings.author])

    def home(self) -> Head:
        hero, author = self.site.hero, self.settings.author
        title = f"{author} · {hero.label}" if hero.label else author
        return self._head(
            "/",
            title,
            hero.intro or self.words["description"],
            {"@type": "WebPage", "name": title, "about": {"@id": self.person["@id"]}},
            image=self.fallback(),
        )

    def item(self, item, crumbs) -> Head:
        description = item.excerpt or item.heading  # the excerpt is the subtitle, where there is one
        image = self.image(item.image, item.caption if item.kind == "photo" else "")
        if item.kind == "page":
            node = {"@type": "WebPage", "name": item.heading}
            kind = "website"
            image = image or self.fallback()
        else:
            node = {
                "@type": "Article" if item.category.section.ordered else "BlogPosting",
                "headline": item.heading[:HEADLINE_LENGTH],
                "description": excerpt(description, DESCRIPTION_LENGTH),
                "datePublished": item.date.isoformat(timespec="seconds"),
                "dateModified": (item.updated or item.date).isoformat(timespec="seconds"),
                "author": {"@id": self.person["@id"]},
                "publisher": {"@id": self.person["@id"]},
                "mainEntityOfPage": self.base + item.url,
                "articleSection": item.category.section.name,
                "inLanguage": self.theme.language,
            }
            if image:
                node["image"] = image["url"]
            kind = "article"
            image = image or self.card
        return self._head(
            item.url,
            self._titled(item.heading),
            description,
            node,
            crumbs,
            type=kind,
            image=image,
            published=item.date if kind == "article" else None,
            modified=item.updated if kind == "article" else None,
        )

    def category(self, cat, url: str, page: int, crumbs) -> Head:
        name = ucfirst(cat.name)
        title = self._titled(name, f"{self.words['page']} {page}") if page > 1 else self._titled(name)
        description = cat.description or cat.intro_text or f"{name} {self.words['by']} {self.settings.author}."
        cover = cat.image or next((i.image for i in cat.items if i.image), None)
        return self._head(
            url,
            title,
            description,
            {"@type": "CollectionPage", "name": name},
            crumbs,
            image=self.image(cover) or self.card,
        )

    def extra(self, extra, crumbs) -> Head:
        description = extra.description or self.words["description"]
        return self._head(
            extra.url,
            self._titled(extra.label),
            description,
            {"@type": "WebPage", "name": extra.label},
            crumbs,
            image=self.card,
        )

    def not_found(self) -> Head:
        return Head(
            title=self._titled(self.words["not_found"]),
            description=self.words["not_found_text"],
            url=f"{self.base}/404.html",
            noindex=True,
        )
