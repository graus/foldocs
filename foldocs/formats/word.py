"""A Word file (.docx), read with mammoth, whose HTML is close to clean already: Word's styles map
to the tags they mean (as does a style made from one of them), a run in a monospace font is code, as
in a Docs export, and a bookmark on a heading (where a table of contents leads) is the heading's id."""

import io
import re
import zipfile
import zlib
from xml.etree import ElementTree

import mammoth
from bs4 import BeautifulSoup

from ..xmlsafe import check_word_file, untrusted_xml
from . import DocText, Reading, code_by_font_only, inline_image, take_titles
from .fonts import monospace

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"  # Word's XML namespace
PART_MAX = 4 << 20  # bytes: past it, a Word file's font table or styles aren't read
OLD_WORD = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # how a Word 97-2003 file (.doc) starts
# Word styles to the tags they mean; Title and Subtitle are taken out as the document's.
STYLES = """
p[style-name='Title'] => p.title:fresh
p[style-name='Subtitle'] => p.subtitle:fresh
p[style-name='Quote'] => blockquote:fresh
p[style-name='Intense Quote'] => blockquote:fresh
r[style-name='Code'] => code
r[style-name='HTML Code'] => code
br[type='page'] => br
br[type='column'] => br
"""
# The built-in styles that mean something, by name (lower case); a style based on one means it too.
MEANT = {
    "title": "p.title:fresh",
    "subtitle": "p.subtitle:fresh",
    "quote": "blockquote:fresh",
    "intense quote": "blockquote:fresh",
    **{f"heading {n}": f"h{n}:fresh" for n in range(1, 7)},
}
# A simple field (a caption's number, a date), as its tags: mammoth leaves out its text.
SIMPLE_FIELD = re.compile(rb"""</?(?:\w+:)?fldSimple\b(?:[^>"']|"[^"]*"|'[^']*')*>""")
# A table of contents' page number, after the tab that ends its entry's text.
PAGE_NUMBER = re.compile(r"\t\s*([0-9]+|[ivx]{1,5})\s*$", re.IGNORECASE)  # not "mid", "civil"


def read(file: Reading) -> DocText:
    """A Word file; its images come along as data: URIs."""
    return clean(word_html(file.download()))


def clean(html: str) -> DocText:
    """mammoth's HTML as clean HTML."""
    body = BeautifulSoup(html, "html.parser")
    for box in body.find_all("input", type="checkbox"):  # a form's checkbox, as Word shows it
        box.replace_with("☒" if box.has_attr("checked") else "☐")
    for heading in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        bookmarks = []
        for a in heading.find_all("a", id=True, href=False):
            bookmarks.append(a["id"])
            a.unwrap()  # it marks a place; any text in it stays
        if bookmarks and not heading.get("id"):
            heading["id"] = bookmarks.pop(0)
        for a in body.find_all("a", href=[f"#{b}" for b in bookmarks]):  # its other bookmarks
            a["href"] = f"#{heading.get('id', '')}"
    internal = [a for a in body.find_all("a", href=True) if a["href"].startswith("#")]
    targets = {a["href"] for a in internal}
    for a in body.find_all("a", id=True, href=False):  # a bookmark no link leads to (Word's _GoBack)
        if f"#{a['id']}" not in targets:
            a.unwrap()
    for a in internal:  # a table of contents' entry, without its page number
        if (texts := a.find_all(string=True)) and PAGE_NUMBER.search(texts[-1]):
            texts[-1].replace_with(PAGE_NUMBER.sub("", texts[-1]))
    code_by_font_only(body)
    title, subtitle = take_titles(body)
    return DocText(body, inline_image, title, subtitle)


def word_html(data: bytes) -> str:
    """A Word file as HTML, as mammoth writes it. Raises ValueError, saying why, for a file that
    is no Word file or one mammoth can't read."""
    if data.startswith(OLD_WORD):
        raise ValueError("an old Word file (.doc) named .docx: save it as Word (.docx) to publish it")
    try:
        check_word_file(data)
        data = _fields_as_text(data)
    except (zipfile.BadZipFile, zlib.error, NotImplementedError) as e:  # no zip, or a part that won't unpack
        raise ValueError(f"not a Word file that can be read ({type(e).__name__}: {e})") from None
    font_table, styles = _parts(data, "word/fontTable.xml", "word/styles.xml")
    fixed = _fixed_pitch_fonts(font_table)
    code_runs = mammoth.transforms.run(
        lambda run: run.copy(style_name="Code") if run.font and monospace(run.font, fixed) else run
    )
    try:
        # A Word file can point at images outside itself (file://, http://); the folder is untrusted,
        # so those are never followed (mammoth's default; said here, where it matters).
        result = mammoth.convert_to_html(
            io.BytesIO(data),
            style_map=_derived_styles(styles) + STYLES,
            external_file_access=False,
            transform_document=code_runs,
        )
    except Exception as e:  # mammoth trusts the file's structure, and fails where a file breaks it
        raise ValueError(f"a Word file that can't be read ({type(e).__name__}: {e})") from e
    return result.value


def _parts(data: bytes, *names: str) -> list[ElementTree.Element | None]:
    """Small XML parts of a Word file (its font table, its styles), each None where it has none,
    or one too large or not XML."""
    found = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in names:
            try:
                if z.getinfo(name).file_size > PART_MAX:  # a real one is well under a megabyte
                    raise KeyError(name)
                found.append(untrusted_xml(z.read(name)))
            except (KeyError, ElementTree.ParseError):
                found.append(None)
    return found


def _fields_as_text(data: bytes) -> bytes:
    """A Word file with every simple field (w:fldSimple: a caption's number as Word writes it, a
    date) as the text it shows, which mammoth would leave out. Only a file with one is made again,
    its images and other parts stored as they are, not packed again."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        fields = {
            info.filename: SIMPLE_FIELD.sub(b"", part)
            for info in z.infolist()
            if info.filename.endswith(".xml") and b"fldSimple" in (part := z.read(info))
        }
        if not fields:
            return data
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as fixed:
            for info in z.infolist():
                if info.filename in fields:
                    fixed.writestr(info.filename, fields[info.filename], zipfile.ZIP_DEFLATED)
                else:
                    fixed.writestr(info.filename, z.read(info), zipfile.ZIP_STORED)
    return out.getvalue()


def _fixed_pitch_fonts(table: ElementTree.Element | None) -> frozenset[str]:
    """The fonts a Word file's font table marks as monospace (<w:pitch w:val="fixed"/>), as Word,
    LibreOffice and Collabora write it; empty when the file has no such table."""
    if table is None:
        return frozenset()
    return frozenset(
        font.get(f"{{{W}}}name", "").lower()
        for font in table.iter(f"{{{W}}}font")
        if (pitch := font.find(f"{{{W}}}pitch")) is not None and pitch.get(f"{{{W}}}val") == "fixed"
    )


def _derived_styles(styles: ElementTree.Element | None) -> str:
    """Style map lines for the paragraph styles made from one that means something: a template's
    "Chapter" based on Heading 1, a "Kop 2" someone made from Heading 2. A style at an outline
    level is a heading of that level whatever it is based on (and at body level, none: Word's TOC
    Heading, based on Heading 1)."""
    if styles is None:
        return ""

    def val(style: ElementTree.Element, path: str) -> str | None:
        found = style.find(path.replace("w:", f"{{{W}}}"))
        return found.get(f"{{{W}}}val") if found is not None else None

    found = {  # a paragraph style's ID -> its name, the ID it is based on, its outline level
        style.get(f"{{{W}}}styleId"): (
            val(style, "w:name") or "",
            val(style, "w:basedOn"),
            val(style, "w:pPr/w:outlineLvl"),
        )
        for style in styles.iter(f"{{{W}}}style")
        if style.get(f"{{{W}}}type") == "paragraph"
    }
    lines = []
    for sid, (name, _, _) in found.items():
        if not name or name.lower() in MEANT or "'" in name or "\\" in name:  # mammoth's own, or a name it can't match
            continue
        current, seen = sid, set()
        while current in found and current not in seen:
            seen.add(current)
            base_name, based, level = found[current]
            if level is not None:
                if level.isascii() and level.isdigit() and int(level) < 6:
                    lines.append(f"p[style-name='{name}'] => h{int(level) + 1}:fresh")
                break
            if base_name.lower() in MEANT:
                lines.append(f"p[style-name='{name}'] => {MEANT[base_name.lower()]}")
                break
            current = based
    return "".join(f"{line}\n" for line in lines)
