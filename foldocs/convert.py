"""Turn a Google Docs export (or a Word file) into clean, semantic HTML.

The Docs export styles everything inline: bold is a span with font-weight:700, text in a
monospace font (a file name, a setting) is code, and nested lists are flat sibling lists with
their level in a class name. Word files are first turned into HTML of the same shape
(word_html). Either way the document is rebuilt from an allowlist of tags and link schemes, so
none of the source's markup or styling reaches the site.
"""

import io
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

import mammoth
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from .text import plain, slugify
from .xmlsafe import check_word_file, untrusted_xml

# Given an <img> src from the export, returns the processed image (see images.process),
# or None to leave it out.
ImageHandler = Callable[[str], dict | None]
# Renders a processed image to <img> attributes (see images.attrs).
ImageAttrs = Callable[..., dict]  # (info, sizes=...) -> the <img>'s attributes

LIST_LEVEL = re.compile(r"lst-kix_\w+-(\d+)")
COMMENT_LINK = re.compile(r"^#cmnt")
# Links that may reach the site: web, mail and phone links, and relative or in-page ones.
SAFE_LINK = re.compile(r"^(https?:|mailto:|tel:|#|/)", re.IGNORECASE)
# A Docs page's text width when the export doesn't say (Letter with 1" margins), in px.
PAGE_WIDTH = 624
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"  # Word's XML namespace
THEME_IDS = ("main", "page-h", "topics-h", "archive-title", "cta-title")  # ids the themes use on a page
FONT_TABLE_MAX = 1 << 20  # bytes: past it, a Word file's font table isn't read
# Fonts that make text code (a file name, a setting). A Word file says which of its fonts are
# monospace (fixed pitch, in its font table); a Docs export names only the family. These are the
# families Google Fonts files as Monospace (from https://fonts.google.com/metadata/fonts), with
# the monospace fonts Docs and Word offer that aren't Google Fonts.
MONOSPACE = frozenset(
    name.lower()
    for name in (
        "Anonymous Pro", "Azeret Mono", "B612 Mono", "Chivo Mono", "Courier Prime", "Cousine", "Cutive Mono",
        "DM Mono", "Datatype", "Fira Code", "Fira Mono", "Fragment Mono", "Geist Mono", "Google Sans Code",
        "Hibur Mono", "IBM Plex Mono", "Inconsolata", "Intel One Mono", "Iosevka Charon", "Iosevka Charon Mono",
        "JetBrains Mono", "Kode Mono", "LXGW WenKai Mono TC", "Lekton", "Libertinus Mono", "Lilex",
        "M PLUS 1 Code", "Major Mono Display", "Martian Mono", "Monofett", "Nova Mono", "Overpass Mono",
        "Oxygen Mono", "PT Mono", "Red Hat Mono", "Reddit Mono", "Roboto Mono", "Share Tech Mono", "Sixtyfour",
        "Sixtyfour Convergence", "Sometype Mono", "Source Code Pro", "Space Mono", "Spline Sans Mono",
        "Syne Mono", "Ubuntu Mono", "Ubuntu Sans Mono", "VT323", "Victor Mono", "Workbench", "Xanh Mono",
        # not Google Fonts
        "Courier New", "Courier", "Consolas", "Lucida Console", "Lucida Sans Typewriter", "Menlo", "Monaco",
        "SF Mono", "Cascadia Code", "Cascadia Mono", "Andale Mono", "Liberation Mono", "DejaVu Sans Mono",
        "monospace",
    )
)  # fmt: skip


def _monospace(font_family: str, also: frozenset[str] = frozenset()) -> bool:
    """Whether a CSS font-family (or a Word font name) is a monospace font."""
    names = (name.strip().strip("\"'").lower() for name in font_family.split(","))
    return any(name in MONOSPACE or name in also for name in names)


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


# Word styles to the classes and tags a Docs export uses, so both take the same road.
WORD_STYLES = """
p[style-name='Title'] => p.title:fresh
p[style-name='Subtitle'] => p.subtitle:fresh
p[style-name='Quote'] => blockquote:fresh
p[style-name='Intense Quote'] => blockquote:fresh
r[style-name='Code'] => code
r[style-name='HTML Code'] => code
"""


def convert(raw: str, image: ImageHandler, attrs: ImageAttrs) -> Doc:
    return _Converter(image, attrs).run(raw)


def word_html(data: bytes) -> str:
    """A Word file as HTML for convert(); its images come along as data: URIs."""
    check_word_file(data)
    fixed = _fixed_pitch_fonts(data)
    # A run in a monospace font is code, as in a Docs export; Word's own Code styles map above.
    code_runs = mammoth.transforms.run(
        lambda run: run.copy(style_name="Code") if run.font and _monospace(run.font, fixed) else run
    )
    # A Word file can point at images outside itself (file://, http://); the folder is untrusted,
    # so those are never followed (mammoth's default; said here, where it matters).
    result = mammoth.convert_to_html(
        io.BytesIO(data), style_map=WORD_STYLES, external_file_access=False, transform_document=code_runs
    )
    return f"<html><body>{result.value}</body></html>"


def _fixed_pitch_fonts(data: bytes) -> frozenset[str]:
    """The fonts a Word file's font table marks as monospace (<w:pitch w:val="fixed"/>), as Word,
    LibreOffice and Collabora write it; empty when the file has no such table."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if z.getinfo("word/fontTable.xml").file_size > FONT_TABLE_MAX:  # a real one is a few kB
                return frozenset()
            table = untrusted_xml(z.read("word/fontTable.xml"))
    except (KeyError, zipfile.BadZipFile, ElementTree.ParseError):
        return frozenset()
    return frozenset(
        font.get(f"{{{W}}}name", "").lower()
        for font in table.iter(f"{{{W}}}font")
        if (pitch := font.find(f"{{{W}}}pitch")) is not None and pitch.get(f"{{{W}}}val") == "fixed"
    )


# The inline tags mammoth writes, as the tags the site keeps.
WORD_INLINE = {"strong": "strong", "b": "strong", "em": "em", "i": "em", "s": "s", "code": "code"}


def _style(tag: Tag) -> dict[str, str]:
    pairs = (decl.split(":", 1) for decl in tag.get("style", "").split(";") if ":" in decl)
    return {k.strip(): v.strip() for k, v in pairs}


def _px(length: str) -> float | None:
    """A CSS length in px or pt as px, or None."""
    m = re.fullmatch(r"([\d.]+)(px|pt)", length.strip())
    return float(m[1]) * (4 / 3 if m[2] == "pt" else 1) if m else None


def _indented(tag: Tag) -> bool:
    style = _style(tag)
    return any(re.match(r"[1-9]", style.get(key, "")) for key in ("margin-left", "padding-left"))


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
    def __init__(self, image: ImageHandler, attrs: ImageAttrs):
        self.image = image
        self.attrs = attrs
        self.soup = BeautifulSoup("", "html.parser")  # factory for the new tags
        self.title = self.subtitle = None
        self.images: list[dict] = []
        self.heading_ids: dict[str, str] = {}  # Google's heading id -> our slug
        self.slugs: set[str] = set(THEME_IDS)  # every heading's id so far, and the themes' own
        self.page_width = PAGE_WIDTH

    def new(self, name: str, **attrs) -> Tag:
        return self.soup.new_tag(name, attrs=attrs)

    def run(self, raw: str) -> Doc:
        body = BeautifulSoup(raw, "html.parser").body
        if width := _px(_style(body).get("max-width", "")):  # the page's text width, in the export
            self.page_width = width
        blocks = self._blocks(body.find_all(recursive=False))
        if self.title is None and blocks and re.fullmatch(r"h[1-6]", blocks[0].name):
            # No paragraph styled "Title": a heading that opens the doc is its title.
            self.title = plain(blocks.pop(0))
        headings = [b for b in blocks if re.fullmatch(r"h[1-6]", b.name)]
        if headings and (shift := min(int(h.name[1]) for h in headings) - 2) > 0:
            for h in headings:  # the highest heading left becomes an <h2>
                h.name = f"h{int(h.name[1]) - shift}"
        links = [a for block in blocks for a in block.find_all("a", href=True)]
        for a in links:  # links to headings within the doc
            if a["href"].startswith("#h."):
                a["href"] = "#" + self.heading_ids.get(a["href"][1:], "")
        return Doc(
            title=self.title,
            subtitle=self.subtitle,
            html="\n".join(str(b) for b in blocks),
            text=" ".join(plain(b) for b in blocks if b.name == "p"),
            images=self.images,
            links=[a["href"] for a in links],
            first=_first(blocks),
        )

    def _blocks(self, elements: list[Tag]) -> list[Tag]:
        out, i = [], 0
        while i < len(elements):
            if elements[i].name in ("ul", "ol"):
                j = i
                while j < len(elements) and elements[j].name in ("ul", "ol"):
                    j += 1
                out += self._lists(elements[i:j])
                i = j
            else:
                out += self._block(elements[i])
                i += 1
        return out

    def _block(self, el: Tag) -> list[Tag]:
        classes = el.get("class", [])
        if el.name == "p" and "title" in classes and self.title is None:
            self.title = plain(el)
            return []
        if el.name == "p" and "subtitle" in classes and self.subtitle is None:
            self.subtitle = plain(el)
            return []
        if re.fullmatch(r"h[1-6]", el.name):
            # The page title is the <h1>, so the doc's headings move one level down.
            blocks = self._paragraph(el, f"h{min(int(el.name[1]) + 1, 6)}")
            if blocks and blocks[0].name.startswith("h"):
                slug = self._heading_slug(plain(blocks[0]))
                blocks[0]["id"] = slug
                if el.get("id"):
                    self.heading_ids[el["id"]] = slug
            return blocks
        if el.name == "blockquote" or (el.name == "p" and _indented(el)):
            # Docs has no quote style; an indented paragraph is how people write one.
            return self._paragraph(el, "blockquote")
        if el.name == "hr":
            return [self.new("hr")]
        if el.name == "table":
            return [self._table(el)]
        if el.name == "div":
            if el.find("a", href=COMMENT_LINK):  # a reviewer's comment, not content
                return []
            return self._blocks(el.find_all(recursive=False))
        return self._paragraph(el, "p")

    def _heading_slug(self, text: str) -> str:
        """An id for a heading, unique on the page: a second "Notes" is notes-2."""
        base = slug = slugify(text, "section")
        n = 2
        while slug in self.slugs:
            slug, n = f"{base}-{n}", n + 1
        self.slugs.add(slug)
        return slug

    def _paragraph(self, el: Tag, name: str) -> list[Tag]:
        """The text as one block, followed by a figure per image it contained."""
        imgs = [img.extract() for img in el.find_all("img")]
        tag = self.new(name)
        self._inline(el, tag)
        blocks = [tag] if tag.get_text(strip=True) else []
        return blocks + [fig for img in imgs if (fig := self._figure(img))]

    def _figure(self, img: Tag) -> Tag | None:
        info = self.image(img.get("src", ""))
        if not info:
            return None
        self.images.append(info)
        fig = self.new("figure")
        # An image the author made smaller than the page keeps that share of the text column.
        width = _px(_style(img).get("width", ""))
        share = round(100 * width / self.page_width) if width and width < 0.9 * self.page_width else 100
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
            elif child.name in WORD_INLINE:  # from a Word file: mammoth writes tags, not styled spans
                tag = self.new(WORD_INLINE[child.name])
                dst.append(tag)
                self._inline(child, tag)
            elif child.name in ("span", "sup", "sub"):
                if child.find("a", href=COMMENT_LINK):
                    continue
                target = dst
                for wrapper in self._wrappers(child):
                    tag = self.new(wrapper)
                    target.append(tag)
                    target = tag
                self._inline(child, target)
            else:
                self._inline(child, dst)

    @staticmethod
    def _wrappers(span: Tag) -> list[str]:
        style = _style(span)
        wrappers = []
        if style.get("font-weight") in ("700", "800", "900", "bold"):
            wrappers.append("strong")
        if style.get("font-style") == "italic":
            wrappers.append("em")
        if _monospace(style.get("font-family", "")):
            wrappers.append("code")
        if "line-through" in style.get("text-decoration", ""):
            wrappers.append("s")
        if span.name == "sup" or style.get("vertical-align") == "super":
            wrappers.append("sup")
        elif span.name == "sub" or style.get("vertical-align") == "sub":
            wrappers.append("sub")
        return wrappers

    def _link(self, a: Tag, dst: Tag) -> None:
        href = a.get("href", "")
        if COMMENT_LINK.match(href):
            return
        if href.startswith(("https://www.google.com/url", "http://www.google.com/url")):
            href = parse_qs(urlparse(href).query).get("q", [href])[0]  # Google's redirect wrapper
        if not SAFE_LINK.match(href.strip()):  # also drops javascript: and data: links; the text stays
            self._inline(a, dst)
            return
        tag = self.new("a", href=href)
        if a.get("id", "").startswith("ftnt"):  # footnote references point at these
            tag["id"] = a["id"]
        dst.append(tag)
        self._inline(a, tag)

    def _lists(self, elements: list[Tag]) -> list[Tag]:
        """Nest Google's flat run of lists by the level in their class names."""
        result: list[Tag] = []
        stack: list[tuple[int, Tag]] = []
        for el in elements:
            m = LIST_LEVEL.search(" ".join(el.get("class", [])))
            level = int(m.group(1)) if m else 0
            while stack and stack[-1][0] > level:
                stack.pop()
            if stack and stack[-1][0] == level and stack[-1][1].name == el.name:
                lst = stack[-1][1]
            else:
                if stack and stack[-1][0] == level:  # same level, other list type
                    stack.pop()
                lst = self.new(el.name)
                if stack:
                    parent = stack[-1][1]
                    items = parent.find_all("li", recursive=False)
                    if not items:
                        items = [self.new("li")]
                        parent.append(items[0])
                    items[-1].append(lst)
                else:
                    result.append(lst)
                stack.append((level, lst))
            for item in el.find_all("li", recursive=False):
                # A list inside the item (how Word files nest lists) is nested here too, not its text.
                nested = [sub.extract() for sub in item.find_all(["ul", "ol"], recursive=False)]
                blocks = self._paragraph(item, "li")
                if nested and not any(b.name == "li" for b in blocks):
                    blocks.insert(0, self.new("li"))  # an item that holds only a list
                for block in blocks:
                    if block.name == "li":
                        lst.append(block)
                    elif lst.find_all("li", recursive=False):  # an image in a list item
                        lst.find_all("li", recursive=False)[-1].append(block)
                if nested:
                    last = lst.find_all("li", recursive=False)[-1]
                    for sub in self._lists(nested):
                        last.append(sub)
        return result

    def _table(self, el: Tag) -> Tag:
        """A table; its first row is its header (<th> cells, read out as such) when the source
        marks it so (Word's header row) or every cell in it is bold, which is how people make one."""
        rows = []
        for tr in el.select(":scope > thead > tr, :scope > tbody > tr, :scope > tr"):  # Word's header in thead
            row = self.new("tr")
            for td in tr.find_all(["td", "th"], recursive=False):
                cell = self.new("td")
                blocks = self._blocks(td.find_all(recursive=False))
                if len(blocks) == 1 and blocks[0].name == "p":
                    blocks = list(blocks[0].children)
                for block in blocks:
                    cell.append(block)
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
