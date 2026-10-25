"""A Word file (.docx), read with mammoth, whose HTML is close to clean already: Word's styles map
to the tags they mean, and a run in a monospace font is code, as in a Docs export."""

import io
import zipfile
from xml.etree import ElementTree

import mammoth
from bs4 import BeautifulSoup

from ..xmlsafe import check_word_file, untrusted_xml
from . import DocText, inline_image, take_titles
from .fonts import monospace

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"  # Word's XML namespace
FONT_TABLE_MAX = 1 << 20  # bytes: past it, a Word file's font table isn't read
# Word styles to the tags they mean; Title and Subtitle are taken out as the document's.
STYLES = """
p[style-name='Title'] => p.title:fresh
p[style-name='Subtitle'] => p.subtitle:fresh
p[style-name='Quote'] => blockquote:fresh
p[style-name='Intense Quote'] => blockquote:fresh
r[style-name='Code'] => code
r[style-name='HTML Code'] => code
"""


def read(data: bytes) -> DocText:
    """A Word file; its images come along as data: URIs."""
    body = BeautifulSoup(word_html(data), "html.parser").body
    title, subtitle = take_titles(body)
    return DocText(str(body), inline_image, title, subtitle)


def word_html(data: bytes) -> str:
    """A Word file as HTML, as mammoth writes it."""
    check_word_file(data)
    fixed = _fixed_pitch_fonts(data)
    code_runs = mammoth.transforms.run(
        lambda run: run.copy(style_name="Code") if run.font and monospace(run.font, fixed) else run
    )
    # A Word file can point at images outside itself (file://, http://); the folder is untrusted,
    # so those are never followed (mammoth's default; said here, where it matters).
    result = mammoth.convert_to_html(
        io.BytesIO(data), style_map=STYLES, external_file_access=False, transform_document=code_runs
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
