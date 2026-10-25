"""A Google Doc, as the service exports it: HTML that styles everything inline. Bold is a span
with font-weight:700, text in a monospace font is code, a quote is an indented paragraph, nested
lists are flat sibling lists with their level in a class name, links go through Google's
redirect, and reviewers' comments come along. Here all that becomes the clean HTML it means."""

import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

from ..sources import DocExport, Exports
from . import DocText, Reading, take_titles
from .fonts import monospace

LIST_LEVEL = re.compile(r"lst-kix_\w+-(\d+)")
COMMENT_LINK = re.compile(r"^#cmnt")
# A Docs page's text width when the export doesn't say (Letter with 1" margins), in px.
PAGE_WIDTH = 624


def read(file: Reading) -> DocText:
    if not isinstance(file.source, Exports):
        raise ValueError(f"A {file.source.kind} folder has no Google Docs to export")
    export = file.source.export_doc(file.entry)
    return clean(export, file.source.fetch)


def clean(export: DocExport, fetch) -> DocText:
    """An export as clean HTML; its images are the ones bundled with it, else fetched by URL."""
    soup = BeautifulSoup(export.html, "html.parser")
    body = soup.body
    _drop_comments(body)
    title, subtitle = take_titles(body)
    page_width = _px(_style(body).get("max-width", "")) or PAGE_WIDTH
    for img in body.find_all("img"):
        # An image the author made smaller than the page keeps that share of the text column.
        width = _px(_style(img).get("width", ""))
        if width and width < 0.9 * page_width:
            img["data-width"] = str(round(100 * width / page_width))
    for span in body.find_all(["span", "sup", "sub"]):
        _restyle(soup, span)
    for a in body.find_all("a", href=True):
        if a["href"].startswith(("https://www.google.com/url", "http://www.google.com/url")):
            a["href"] = parse_qs(urlparse(a["href"]).query).get("q", [a["href"]])[0]  # Google's redirect wrapper
        if a.get("id") and not a["id"].startswith("ftnt"):  # footnote references point at those
            del a["id"]
    # Where text is a block of its own: the page, a box (div) or a table cell.
    for holder in [body, *body.find_all(["div", "td", "th"])]:
        for p in holder.find_all("p", recursive=False):
            if _indented(p):  # Docs has no quote style; an indented paragraph is how people write one
                p.name = "blockquote"
        _nest_lists(soup, holder)
    return DocText(body, lambda src: export.files.get(src) or fetch(src), title, subtitle)


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


def _drop_comments(body: Tag) -> None:
    """A reviewer's comments, and the marks in the text that point at them: not content."""
    for a in body.find_all("a", href=COMMENT_LINK):
        if a.decomposed:  # in a comment already dropped
            continue
        holder = a.find_parent("div") or a.find_parent(["sup", "span"])
        (holder if holder and holder is not body else a).decompose()


def _restyle(soup: BeautifulSoup, span: Tag) -> None:
    """A styled span as the tags its style means (<strong>, <em>, <code>…), or just its text."""
    style, names = _style(span), []
    if style.get("font-weight") in ("700", "800", "900", "bold"):
        names.append("strong")
    if style.get("font-style") == "italic":
        names.append("em")
    if monospace(style.get("font-family", "")):
        names.append("code")
    if "line-through" in style.get("text-decoration", ""):
        names.append("s")
    if span.name == "sup" or style.get("vertical-align") == "super":
        names.append("sup")
    elif span.name == "sub" or style.get("vertical-align") == "sub":
        names.append("sub")
    if not names:
        span.unwrap()
        return
    outer = inner = soup.new_tag(names[0])
    for name in names[1:]:
        inner.append(tag := soup.new_tag(name))
        inner = tag
    for child in list(span.contents):
        inner.append(child.extract())
    span.replace_with(outer)


def _nest_lists(soup: BeautifulSoup, parent: Tag) -> None:
    """Nest every run of Google's flat lists by the level in their class names."""
    runs, run = [], []
    for el in parent.find_all(recursive=False):
        if el.name in ("ul", "ol"):
            run.append(el)
            continue
        if run:
            runs.append(run)
        run = []
    if run:
        runs.append(run)
    for run in runs:
        nested = _nested(soup, run)
        run[0].insert_before(*nested)
        for el in run:
            el.decompose()


def _nested(soup: BeautifulSoup, elements: list[Tag]) -> list[Tag]:
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
            lst = soup.new_tag(el.name, attrs={"start": el["start"]} if el.get("start") else {})
            if stack:
                parent = stack[-1][1]
                items = parent.find_all("li", recursive=False)
                if not items:
                    items = [soup.new_tag("li")]
                    parent.append(items[0])
                items[-1].append(lst)
            else:
                result.append(lst)
            stack.append((level, lst))
        for item in el.find_all("li", recursive=False):
            lst.append(item.extract())
    return result
