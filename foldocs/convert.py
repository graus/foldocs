"""Make a document a page: the same for every format (see formats/), from the clean HTML its
format reads it into.

Clean HTML is blocks (p, h1-h6, ul and ol with li, table, blockquote, pre with code, hr, and div
only to group them) holding text in a few inline tags (a, strong, em, code, s, sup, sub, br) and
images (img, with data-width for a share of the text's width). An <a> with an id and no address
is a place a link leads to; a table cell can span columns and rows (colspan, rowspan). Text between blocks, in a list
item or a table cell say, is a paragraph of its own.
Here the page is rebuilt from those tags and safe links alone, so no markup or styling a file
carries reaches the site; the headings move a level down below the page's title and get ids of
their own, and each image becomes a figure.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from .text import plain, slugify

# Given an <img> src from the document, returns the processed image (see images.process),
# or None to leave it out.
ImageHandler = Callable[[str], dict | None]
# Renders a processed image to <img> attributes (see images.attrs).
ImageAttrs = Callable[..., dict]  # (info, sizes=...) -> the <img>'s attributes
# Given a link's address, returns it as a link to another file in the folder (a folder: address,
# see formats.Reading.page_link), or None for a link to anything else.
LinkHandler = Callable[[str], str | None]

# Links that may reach the site: web, mail and phone links, relative or in-page ones, and links to
# another file in the folder, which the build makes that page's address.
SAFE_LINK = re.compile(r"^((?i:https?:|mailto:|tel:)|#|/|folder:/)")  # folder: as page_link writes it
THEME_IDS = ("main", "page-h", "topics-h", "archive-title", "cta-title")  # ids the themes use on a page
INLINE = {"strong", "em", "code", "s", "sup", "sub"}
SAME_AS = {"b": "strong", "i": "em", "del": "s", "strike": "s"}  # the tags HTML has for the same thing
HEADING = re.compile(r"h[1-6]")
ANCHOR_ID = re.compile(r"[\w.-]+")  # an id a link in the document may point at
GROUPS = frozenset({"div", "section", "article", "main", "header", "footer", "aside"})  # tags that only group blocks
SPANS = {"colspan": 1000, "rowspan": 65534}  # what a merged table cell spans, at most as HTML allows
BLOCKS = GROUPS | {"p", "ul", "ol", "table", "blockquote", "pre", "hr", "h1", "h2", "h3", "h4", "h5", "h6"}


@dataclass
class Doc:
    title: str | None  # the paragraph styled "Title", if any
    subtitle: str | None  # the paragraph styled "Subtitle", if any
    html: str
    text: str  # plain text of the paragraphs, for excerpts
    images: list[dict] = field(default_factory=list)
    links: list[str] = field(default_factory=list)  # every link's address, in order (an intro's are contact links)
    # Its first paragraph, heading and list, as text: what a root intro shows atop the home page
    first: dict = field(default_factory=lambda: {"paragraph": None, "heading": None, "list": []})


def convert(
    html: str | Tag,
    image: ImageHandler,
    attrs: ImageAttrs,
    title: str | None = None,
    subtitle: str | None = None,
    link: LinkHandler | None = None,
) -> Doc:
    """A document's clean HTML (or the HTML parsed, which is taken apart) as a page; its title and
    subtitle, where its format found them."""
    return _Converter(image, attrs, title, subtitle, link).run(html)


def _header_row(source: Tag, row: Tag) -> bool:
    """Whether a table's first row is its header: marked so, or every cell in it bold."""
    if source.parent.name == "thead" or source.find("th", recursive=False):
        return True
    cells = row.find_all("td")
    return bool(cells) and all(_bold(cell) for cell in cells)


def _bold(tag: Tag) -> bool:
    """Whether all of a tag's text is bold (and there is some)."""
    texts = [s for s in tag.find_all(string=True) if s.strip()]
    return bool(texts) and all(s.find_parent("strong") for s in texts)


def _shared_sizes(share: int) -> str:
    """How wide an image shows that the author made a share of the text's width, for its `sizes`.
    The text is about 42.5rem wide in every bundled theme (images.SIZES), the screen less its
    margins on a phone."""
    return f"(min-width: 48rem) {42.5 * share / 100:.1f}rem, calc((100vw - 2rem) * {share / 100:.2f})"


def _trim(tag: Tag) -> None:
    """Drop the white space a block opens or ends with, such as the line breaks between tags, and
    a line break (<br>) it opens or ends with."""
    for end in (0, -1):
        while tag.contents and (getattr(child := tag.contents[end], "name", None) == "br" or _blank(child)):
            child.extract()
    tag.smooth()  # neighboring strings as one
    for end, strip in ((0, str.lstrip), (-1, str.rstrip)):
        if tag.contents and isinstance(child := tag.contents[end], NavigableString):
            child.replace_with(NavigableString(strip(str(child))))


def _blank(node) -> bool:
    return isinstance(node, NavigableString) and not node.strip()


FIRST = {"paragraph": ("p",), "heading": ("h2",), "list": ("ul", "ol")}


def _first(blocks: list[Tag]) -> dict:
    """The text of the first paragraph and heading, and the items of the first list."""
    found = {kind: next((b for b in blocks if b.name in names), None) for kind, names in FIRST.items()}
    return {
        "paragraph": plain(found["paragraph"]) if found["paragraph"] else None,
        "heading": plain(found["heading"]) if found["heading"] else None,
        "list": [plain(li) for li in found["list"].find_all("li", recursive=False)] if found["list"] else [],
    }


class _Converter:
    def __init__(
        self, image: ImageHandler, attrs: ImageAttrs, title: str | None, subtitle: str | None, link: LinkHandler | None
    ):
        self.image, self.attrs, self.link = image, attrs, link
        self.soup = BeautifulSoup("", "html.parser")  # factory for the new tags
        self.title, self.subtitle = title, subtitle
        self.images: list[dict] = []
        self.headings: list[tuple[Tag, str]] = []  # every heading, with its id in the document
        self.heading_ids: dict[str, str] = {}  # a heading's id in the document -> our slug
        self.slugs: set[str] = set(THEME_IDS)  # the headings' ids so far, and the themes' own
        self.anchors: dict[str, Tag] = {}  # the ids links and list items keep (see _anchor), and where

    def new(self, name: str, **attrs) -> Tag:
        return self.soup.new_tag(name, attrs=attrs)

    def run(self, raw: str | Tag) -> Doc:
        parsed = raw if isinstance(raw, Tag) else BeautifulSoup(raw, "html.parser")
        blocks = self._blocks(parsed.body or parsed)
        title = None
        if self.title is None and blocks and HEADING.fullmatch(blocks[0].name):
            # No paragraph styled "Title": a heading that opens the doc is its title.
            title = blocks.pop(0)
            self.title = plain(title)
        for heading, source_id in self.headings:  # ids once the title is out: it takes none
            if heading is not title:
                heading["id"] = self._unique(slugify(plain(heading), "section"))
                if source_id:
                    self.heading_ids[source_id] = heading["id"]
        headings = [h for b in blocks for h in ([b] if HEADING.fullmatch(b.name) else b.find_all(HEADING))]
        if headings and (shift := min(int(h.name[1]) for h in headings) - 2) > 0:
            for h in headings:  # the highest heading left becomes an <h2>
                h.name = f"h{int(h.name[1]) - shift}"
        links = [a for block in blocks for a in block.find_all("a", href=True)]
        # What a link within the document may lead to: an id on the page as it came out (not one
        # in the title taken out, or in a paragraph left out for having no text).
        on_page = {tag["id"] for block in blocks for tag in [block, *block.find_all(id=True)] if tag.get("id")}
        for a in links:  # links within the document: to a heading by its new id; to nothing, nowhere
            if a["href"].startswith("#") and len(a["href"]) > 1:
                target = self.heading_ids.get(a["href"][1:], a["href"][1:])
                a["href"] = "#" + (target if target in on_page else "")
        return Doc(
            title=self.title,
            subtitle=self.subtitle,
            html="\n".join(str(b) for b in blocks),
            text=" ".join(plain(b) for b in blocks if b.name == "p"),
            images=self.images,
            links=[a["href"] for a in links],
            first=_first(blocks),
        )

    def _blocks(self, holder: Tag) -> list[Tag]:
        """The blocks a tag holds. Text and inline tags between them (as in a tight Markdown list
        item, or a table cell) are a paragraph of their own."""
        out, loose = [], []
        for child in list(holder.children):
            if isinstance(child, Tag) and child.name in BLOCKS:
                out += self._loose(loose) + self._block(child)
                loose = []
            elif not isinstance(child, Comment):
                loose.append(child)
        return out + self._loose(loose)

    def _loose(self, nodes: list) -> list[Tag]:
        """Text and inline tags as a paragraph, if there is anything to read in them."""
        if not any(isinstance(node, Tag) or node.strip() for node in nodes):
            return []
        p = self.new("p")
        p.extend([node.extract() for node in nodes])
        return self._paragraph(p, "p")

    def _block(self, el: Tag) -> list[Tag]:
        if HEADING.fullmatch(el.name):
            # The page title is the <h1>, so the doc's headings move one level down.
            blocks = self._paragraph(el, f"h{min(int(el.name[1]) + 1, 6)}")
            if blocks and blocks[0].name.startswith("h"):
                self.headings.append((blocks[0], el.get("id", "")))
            return blocks
        if el.name == "blockquote":
            quote = self._holder(el, "blockquote")
            return [quote] if quote.contents else []
        if el.name == "hr":
            return [self.new("hr")]
        if el.name == "pre":  # a block of code: its lines as they are
            pre, code = self.new("pre"), self.new("code")
            code.string = el.get_text().rstrip("\n")
            pre.append(code)
            return [pre]
        if el.name in ("ul", "ol"):
            return [self._list(el)]
        if el.name == "table":
            return [self._table(el)]
        if el.name in GROUPS:
            return self._blocks(el)
        return self._paragraph(el, "p")

    def _unique(self, base: str) -> str:
        """An id unique on the page: a second "Notes" heading is notes-2, as is one where the
        document has an id "notes" of its own (a bookmark's, a footnote's)."""
        slug, n = base, 2
        while slug in self.slugs or slug in self.anchors:
            slug, n = f"{base}-{n}", n + 1
        self.slugs.add(slug)
        return slug

    def _paragraph(self, el: Tag, name: str) -> list[Tag]:
        """The text as one block, followed by a figure per image it contained."""
        imgs = [img.extract() for img in el.find_all("img")]
        tag = self.new(name)
        self._inline(el, tag)
        _trim(tag)
        blocks = [tag] if tag.get_text(strip=True) else []
        return blocks + [fig for img in imgs if (fig := self._figure(img))]

    def _figure(self, img: Tag) -> Tag | None:
        info = self.image(img.get("src", ""))
        if not info:
            return None
        self.images.append(info)
        fig = self.new("figure")
        width = img.get("data-width", "")
        share = int(width) if width.isascii() and width.isdigit() and 0 < int(width) < 100 else 100
        if share < 100:
            fig["style"] = f"--width: {share}%"
        alt = img.get("alt") or img.get("title") or ""
        attrs = self.attrs(info, sizes=_shared_sizes(share)) if share < 100 else self.attrs(info)
        fig.append(self.new("img", **attrs, alt=alt, loading="lazy", decoding="async"))
        return fig

    def _inline(self, src: Tag, dst: Tag) -> None:
        for child in src.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                dst.append(NavigableString(str(child)))
            elif child.name == "br":
                dst.append(self.new("br"))
            elif child.name == "a":
                self._link(child, dst)
            elif (name := SAME_AS.get(child.name, child.name)) in INLINE:
                if dst.name == name or dst.find_parent(name):  # within the same tag already
                    self._inline(child, dst)
                    continue
                tag = self.new(name)
                dst.append(tag)
                self._inline(child, tag)
            else:  # any other tag: its text only
                self._inline(child, dst)

    def _link(self, a: Tag, dst: Tag) -> None:
        href = a.get("href")
        if href is None:  # a place a link leads to, such as a bookmark, if it has an id it can keep
            tag = self.new("a")
            self._anchor(a, tag)
            if tag.get("id"):
                dst.append(tag)
                self._inline(a, tag)
            else:
                self._inline(a, dst)
            return
        href = (self.link and self.link(href.strip())) or href
        if not SAFE_LINK.match(href.strip()):  # also drops javascript: and data: links; the text stays
            self._inline(a, dst)
            return
        tag = self.new("a", href=href)
        self._anchor(a, tag)
        dst.append(tag)
        self._inline(a, tag)

    def _anchor(self, src: Tag, dst: Tag) -> None:
        """The id a link or list item has in the document, if a link may point at it (a footnote's,
        say) and the page has no such id yet. It keeps it: a heading's id is made up, and makes way."""
        src_id = src.get("id", "")
        if ANCHOR_ID.fullmatch(src_id) and src_id not in self.anchors and src_id not in THEME_IDS:
            self.anchors[src_id] = dst
            dst["id"] = src_id

    def _holder(self, el: Tag, name: str) -> Tag:
        """A tag that holds blocks: a quote, a list item, a table cell. A paragraph that opens it,
        with only lists and images after it, is its text: how the tag reads anyway."""
        tag = self.new(name)
        blocks = self._blocks(el)
        if blocks and blocks[0].name == "p" and all(b.name in ("ul", "ol", "figure") for b in blocks[1:]):
            tag.extend(list(blocks.pop(0).children))
        tag.extend(blocks)
        return tag

    def _list(self, el: Tag) -> Tag:
        """A list, and the lists inside its items."""
        lst = self.new(el.name)
        if el.name == "ol" and (start := el.get("start", "1")).isascii() and start.isdigit() and start != "1":
            lst["start"] = start  # a list that goes on after something in between
        for item in el.find_all("li", recursive=False):
            li = self._holder(item, "li")
            if li.contents:
                self._anchor(item, li)
                lst.append(li)
        return lst

    def _table(self, el: Tag) -> Tag:
        """A table; its first row is its header (<th> cells, read out as such) when the document
        marks it so (Word's header row) or every cell in it is bold, which is how people make one."""
        rows = []
        for tr in el.select(":scope > thead > tr, :scope > tbody > tr, :scope > tr"):
            row = self.new("tr")
            for td in tr.find_all(["td", "th"], recursive=False):
                cell = self._holder(td, "td")
                for span, most in SPANS.items():  # a merged cell
                    if (n := td.get(span, "")).isascii() and n.isdigit() and 1 < int(n) <= most:
                        cell[span] = n
                row.append(cell)
            rows.append((tr, row))
        table = self.new("table")
        if rows and _header_row(*rows[0]):
            head = self.new("thead")
            for cell in rows[0][1].find_all("td"):
                cell.name, cell["scope"] = "th", "col"
                if (only := cell.find(True)) and only.name == "strong" and plain(only) == plain(cell):
                    only.unwrap()  # the header's own weight says it
            head.append(rows.pop(0)[1])
            table.append(head)
        body = self.new("tbody")
        for _, row in rows:
            body.append(row)
        table.append(body)
        wrap = self.new("div", **{"class": "table-wrap"})
        wrap.append(table)
        return wrap
