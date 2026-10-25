"""Write the generated cases of the test corpus (tests/corpus/): Word files made with python-docx,
each showing one thing people leave in a document, and Markdown files no editor would write.

    uv run python scripts/make_corpus.py

The files are committed; run this again only to change a case, then review the change with
UPDATE_CORPUS=1 uv run pytest tests/test_corpus.py. Real files, from the programs themselves, are
made with scripts/office_corpus.py; Google Docs exports are recorded with scripts/record_corpus.py.
"""

import io
import random
import struct
import sys
import zipfile
import zlib
from pathlib import Path

import docx
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import parse_xml
from docx.shared import Inches

CORPUS = Path(__file__).resolve().parent.parent / "tests" / "corpus"
NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
    'xmlns:v="urn:schemas-microsoft-com:vml" '
    'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"'
)


def png(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    """A small one-color PNG."""
    row = b"\x00" + bytes(color) * width
    raw = zlib.compress(row * height, 9)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


def add(document, xml: str) -> None:
    """Body XML (paragraphs, tables) at the end of the document, before its section settings."""
    for el in parse_xml(f"<w:body {NS}>{xml}</w:body>"):
        document.element.body.sectPr.addprevious(el)


def p(text: str, style: str = "") -> str:
    props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f'<w:p>{props}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def save(document, case: str) -> bytes:
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def with_parts(data: bytes, parts: dict[str, bytes], rels: list[str], types: list[str]) -> bytes:
    """A Word file with more parts: their relationships from the document, and their content types."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        files = {name: z.read(name) for name in z.namelist()}
    files.update(parts)
    rel_name = "word/_rels/document.xml.rels"
    files[rel_name] = files[rel_name].replace(b"</Relationships>", "".join(rels).encode() + b"</Relationships>")
    files["[Content_Types].xml"] = files["[Content_Types].xml"].replace(
        b"</Types>", "".join(types).encode() + b"</Types>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


def new(title: str) -> docx.Document:
    d = docx.Document()
    d.core_properties.author = "Jan Jansen"
    d.core_properties.comments = ""
    d.add_paragraph(title, style="Title")
    return d


# --- Word ---


def word_tracked_changes() -> bytes:
    d = new("Tracked changes")
    add(
        d,
        '<w:p><w:r><w:t xml:space="preserve">The trip starts </w:t></w:r>'
        '<w:del w:id="1" w:author="Jan Jansen" w:date="2026-10-01T10:00:00Z">'
        '<w:r><w:delText xml:space="preserve">on Monday </w:delText></w:r></w:del>'
        '<w:ins w:id="2" w:author="Jan Jansen" w:date="2026-10-01T10:00:00Z">'
        '<w:r><w:t xml:space="preserve">on Tuesday </w:t></w:r></w:ins>'
        "<w:r><w:t>in Utrecht.</w:t></w:r></w:p>"
        # A paragraph inserted whole, and one deleted whole (its mark too).
        '<w:p><w:pPr><w:rPr><w:ins w:id="3" w:author="Jan Jansen" w:date="2026-10-01T10:00:00Z"/></w:rPr></w:pPr>'
        '<w:ins w:id="4" w:author="Jan Jansen" w:date="2026-10-01T10:00:00Z">'
        "<w:r><w:t>A paragraph added in review.</w:t></w:r></w:ins></w:p>"
        '<w:p><w:pPr><w:rPr><w:del w:id="5" w:author="Jan Jansen" w:date="2026-10-01T10:00:00Z"/></w:rPr></w:pPr>'
        '<w:del w:id="6" w:author="Jan Jansen" w:date="2026-10-01T10:00:00Z">'
        "<w:r><w:delText>A paragraph struck in review.</w:delText></w:r></w:del></w:p>"
        # Moved text: only where it went is the text.
        '<w:p><w:moveFrom w:id="7" w:author="Jan Jansen" w:date="2026-10-01T10:00:00Z">'
        "<w:r><w:t>Moved away.</w:t></w:r></w:moveFrom></w:p>"
        '<w:p><w:moveTo w:id="8" w:author="Jan Jansen" w:date="2026-10-01T10:00:00Z">'
        "<w:r><w:t>Moved here.</w:t></w:r></w:moveTo></w:p>"
        # A change of formatting: the text as it is now, bold.
        '<w:p><w:r><w:rPr><w:b/><w:rPrChange w:id="9" w:author="Jan Jansen" w:date="2026-10-01T10:00:00Z">'
        "<w:rPr/></w:rPrChange></w:rPr><w:t>Now bold.</w:t></w:r></w:p>",
    )
    return save(d, "tracked-changes")


def word_comments() -> bytes:
    d = new("Comments")
    para = d.add_paragraph("The station is ")
    run = para.add_run("ten minutes")
    para.add_run(" from the house.")
    d.add_comment(run, text="Is it not fifteen?", author="Jan Jansen", initials="JJ")
    d.add_paragraph("Bring a coat.")
    return save(d, "comments")


TEXT_BOX = """
<w:p><w:r><w:t xml:space="preserve">Before the box. </w:t></w:r>
<w:r><mc:AlternateContent>
  <mc:Choice Requires="wps"><w:drawing><wp:anchor distT="0" distB="0" distL="0" distR="0" simplePos="0"
      relativeHeight="1" behindDoc="0" locked="0" layoutInCell="1" allowOverlap="1">
    <wp:simplePos x="0" y="0"/><wp:positionH relativeFrom="column"><wp:posOffset>0</wp:posOffset></wp:positionH>
    <wp:positionV relativeFrom="paragraph"><wp:posOffset>0</wp:posOffset></wp:positionV>
    <wp:extent cx="1828800" cy="914400"/><wp:wrapSquare wrapText="bothSides"/><wp:docPr id="1" name="Text Box 1"/>
    <a:graphic><a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">
      <wps:wsp><wps:cNvSpPr txBox="1"/><wps:spPr/>
        <wps:txbx><w:txbxContent><w:p><w:r><w:t>Opening hours: nine to five.</w:t></w:r></w:p></w:txbxContent>
        </wps:txbx>
        <wps:bodyPr/></wps:wsp>
    </a:graphicData></a:graphic></wp:anchor></w:drawing></mc:Choice>
  <mc:Fallback><w:pict><v:shape id="Text Box 1" style="width:144pt;height:72pt" type="#_x0000_t202">
    <v:textbox><w:txbxContent><w:p><w:r><w:t>Opening hours: nine to five.</w:t></w:r></w:p></w:txbxContent></v:textbox>
  </v:shape></w:pict></mc:Fallback>
</mc:AlternateContent></w:r>
<w:r><w:t>After the box.</w:t></w:r></w:p>
"""


def word_text_box() -> bytes:
    d = new("Text box")
    add(d, TEXT_BOX)
    d.add_paragraph("Last line.")
    return save(d, "text-box")


def word_header_footer() -> bytes:
    d = new("Header and footer")
    section = d.sections[0]
    section.header.paragraphs[0].text = "Draft, Jan Jansen"
    section.footer.paragraphs[0].text = "Page footer: example.com"
    d.add_paragraph("The body of the page.")
    return save(d, "header-footer")


def numbering(document) -> tuple[int, int]:
    """A numbered and a bulleted list of three levels each, as Word defines every list (python-docx's
    template defines one level): their numIds."""
    formats = {"decimal": ("decimal", "lowerLetter", "lowerRoman"), "bullet": ("bullet",) * 3}
    element = document.part.numbering_part.element
    ids = []
    for n, levels in enumerate(formats.values(), start=20):
        lvls = "".join(
            f'<w:lvl w:ilvl="{i}"><w:start w:val="1"/><w:numFmt w:val="{fmt}"/>'
            f'<w:lvlText w:val="{"•" if fmt == "bullet" else f"%{i + 1}."}"/><w:pPr>'
            f'<w:ind w:left="{720 * (i + 1)}"/></w:pPr></w:lvl>'
            for i, fmt in enumerate(levels)
        )
        element.insert(0, parse_xml(f'<w:abstractNum {NS} w:abstractNumId="{n}">{lvls}</w:abstractNum>'))
        element.append(parse_xml(f'<w:num {NS} w:numId="{n}"><w:abstractNumId w:val="{n}"/></w:num>'))
        ids.append(n)
    return ids[0], ids[1]


def word_numbering_without_list_style() -> bytes:
    d = new("Numbering without a list style")
    d.add_paragraph("Typed by hand:")
    for line in (
        "1. Pack the bags",
        "2. Lock the door",
        "3) Leave the key",
        "a. with the neighbours",
        "- or under the mat",
    ):
        d.add_paragraph(line)
    d.add_paragraph("Numbered by the paragraph itself, in the Normal style:")
    num, bullet = numbering(d)
    for text, level, num_id in (("First", 0, num), ("Inside it", 1, num), ("Second", 0, num), ("A dot", 0, bullet)):
        add(
            d,
            f'<w:p><w:pPr><w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="{num_id}"/></w:numPr></w:pPr>'
            f"<w:r><w:t>{text}</w:t></w:r></w:p>",
        )
    d.add_paragraph("Then a numbered list with a level skipped:")
    for text, level in (("Top", 0), ("Two levels down", 2), ("Top again", 0)):
        add(
            d,
            f'<w:p><w:pPr><w:pStyle w:val="ListParagraph"/><w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="{num}"/>'
            f"</w:numPr></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>",
        )
    return save(d, "numbering-without-list-style")


def word_dutch_styles() -> bytes:
    """Word in Dutch names its built-in styles in English in the file (Kop1 is only the ID); a style
    someone made is named as they named it, here based on the built-in ones."""
    d = docx.Document()
    styles = d.styles
    styles["Heading 1"].element.styleId = "Kop1"
    for name, base in (("Titel", "Title"), ("Kop 2", "Heading 2"), ("Citaat", "Quote")):
        style = styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        style.base_style = styles[base]
        style.element.styleId = f"Eigen{name.replace(' ', '')}"
    d.add_paragraph("Reisverslag", style="Titel")
    d.add_paragraph("Inleiding", style="Heading 1")
    d.add_paragraph("We vertrokken vroeg.")
    d.add_paragraph("Dag één", style="Kop 2")
    d.add_paragraph("Regen, en toen zon.")
    d.add_paragraph("Wie niet waagt, die niet wint.", style="Citaat")
    return save(d, "dutch-styles")


TOC_ENTRY = (
    '<w:p><w:pPr><w:pStyle w:val="TOC{level}"/><w:tabs><w:tab w:val="right" w:leader="dot" w:pos="9350"/></w:tabs>'
    "</w:pPr>{begin}"
    '<w:hyperlink w:anchor="{anchor}" w:history="1"><w:r><w:t>{text}</w:t></w:r><w:r><w:tab/></w:r>'
    '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r>'
    '<w:instrText xml:space="preserve"> PAGEREF {anchor} \\h </w:instrText></w:r>'
    '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>{page}</w:t></w:r><w:r>'
    '<w:fldChar w:fldCharType="end"/></w:r>'
    "</w:hyperlink></w:p>"
)
TOC_BEGIN = (
    '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r>'
    '<w:instrText xml:space="preserve"> TOC \\o "1-3" \\h \\z \\u </w:instrText></w:r>'
    '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
)


def word_table_of_contents() -> bytes:
    d = new("Table of contents")
    for level in (1, 2):
        style = d.styles.add_style(f"toc {level}", WD_STYLE_TYPE.PARAGRAPH)
        style.element.styleId = f"TOC{level}"
    entries = [(1, "_Toc1", "Getting there", 1), (2, "_Toc2", "By train", 1), (1, "_Toc3", "Staying", 2)]
    toc = "".join(
        TOC_ENTRY.format(level=level, anchor=anchor, text=text, page=page, begin=TOC_BEGIN if i == 0 else "")
        for i, (level, anchor, text, page) in enumerate(entries)
    )
    add(
        d,
        '<w:sdt><w:sdtPr><w:docPartObj><w:docPartGallery w:val="Table of Contents"/><w:docPartUnique/></w:docPartObj>'
        '</w:sdtPr><w:sdtContent><w:p><w:pPr><w:pStyle w:val="TOCHeading"/></w:pPr><w:r><w:t>Contents</w:t></w:r></w:p>'
        f'{toc}<w:p><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p></w:sdtContent></w:sdt>',
    )
    for level, anchor, text, _ in entries:
        add(
            d,
            f'<w:p><w:pPr><w:pStyle w:val="Heading{level}"/></w:pPr>'
            f'<w:bookmarkStart w:id="{anchor[-1]}" w:name="{anchor}"/>'
            f'<w:r><w:t>{text}</w:t></w:r><w:bookmarkEnd w:id="{anchor[-1]}"/></w:p>',
        )
        d.add_paragraph(f"Text about {text.lower()}.")
    return save(d, "table-of-contents")


FOOTNOTES = (
    f'<w:footnotes {NS}><w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
    '<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>'
    '<w:footnote w:id="1"><w:p><w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:footnoteRef/></w:r>'
    '<w:r><w:t xml:space="preserve"> Since 1843, says the sign.</w:t></w:r></w:p></w:footnote>'
    '<w:footnote w:id="2"><w:p><w:r><w:footnoteRef/></w:r><w:r><w:t xml:space="preserve"> See </w:t></w:r>'
    '<w:hyperlink r:id="rIdFootLink"><w:r><w:t>example.com</w:t></w:r></w:hyperlink></w:p>'
    "<w:p><w:r><w:t>A second paragraph in the note.</w:t></w:r></w:p></w:footnote></w:footnotes>"
)
ENDNOTES = (
    f'<w:endnotes {NS}><w:endnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:endnote>'
    '<w:endnote w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:endnote>'
    '<w:endnote w:id="1"><w:p><w:r><w:endnoteRef/></w:r><w:r><w:t xml:space="preserve"> An endnote.</w:t></w:r></w:p>'
    "</w:endnote></w:endnotes>"
)


def word_footnotes() -> bytes:
    d = new("Footnotes")
    add(
        d,
        '<w:p><w:r><w:t xml:space="preserve">The bridge is old.</w:t></w:r>'
        '<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:footnoteReference w:id="1"/></w:r>'
        '<w:r><w:t xml:space="preserve"> The map is online.</w:t></w:r>'
        '<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:footnoteReference w:id="2"/></w:r></w:p>'
        '<w:p><w:r><w:t xml:space="preserve">And at the end.</w:t></w:r>'
        '<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:endnoteReference w:id="1"/></w:r></w:p>',
    )
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    typ = "application/vnd.openxmlformats-officedocument.wordprocessingml."
    foot_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rIdFootLink" Type="{rel}hyperlink" Target="https://example.com/map" TargetMode="External"/>'
        "</Relationships>"
    )
    return with_parts(
        save(d, "footnotes"),
        {
            "word/footnotes.xml": FOOTNOTES.encode(),
            "word/endnotes.xml": ENDNOTES.encode(),
            "word/_rels/footnotes.xml.rels": foot_rels.encode(),
        },
        [
            f'<Relationship Id="rIdFoot" Type="{rel}footnotes" Target="footnotes.xml"/>',
            f'<Relationship Id="rIdEnd" Type="{rel}endnotes" Target="endnotes.xml"/>',
        ],
        [
            f'<Override PartName="/word/footnotes.xml" ContentType="{typ}footnotes+xml"/>',
            f'<Override PartName="/word/endnotes.xml" ContentType="{typ}endnotes+xml"/>',
        ],
    )


def word_tables() -> bytes:
    d = new("Tables")
    d.add_paragraph("A table with merged cells:")
    t = d.add_table(rows=3, cols=3)
    for r, row in enumerate([("Day", "Where", "Cost"), ("Monday", "Utrecht", "€ 12"), ("Tuesday", "", "€ 8")]):
        for c, text in enumerate(row):
            if text:
                t.cell(r, c).paragraphs[0].add_run(text).bold = r == 0
    t.cell(1, 1).merge(t.cell(2, 1))  # one place both days
    t2 = d.add_table(rows=2, cols=2)
    t2.cell(0, 0).merge(t2.cell(0, 1)).text = "Spanning the whole row"
    t2.cell(1, 0).text, t2.cell(1, 1).text = "left", "right"
    d.add_paragraph("A table inside a table:")
    outer = d.add_table(rows=1, cols=2)
    outer.cell(0, 0).text = "Outside"
    inner = outer.cell(0, 1).add_table(rows=2, cols=1)
    inner.cell(0, 0).text, inner.cell(1, 0).text = "Inside one", "Inside two"
    d.add_paragraph("A table with a list and an empty row:")
    t3 = d.add_table(rows=2, cols=2)
    t3.cell(0, 0).text = "Bring"
    t3.cell(0, 1).paragraphs[0].text = "Coat"
    t3.cell(0, 1).add_paragraph("Boots", style="List Bullet")
    t3.cell(0, 1).add_paragraph("Hat", style="List Bullet")
    return save(d, "tables")


def word_images() -> bytes:
    d = new("Images")
    d.add_paragraph("An image on its own line:")
    d.add_picture(io.BytesIO(png(64, 48, (200, 90, 40))), width=Inches(6))
    para = d.add_paragraph("An image in a line of text ")
    para.add_run().add_picture(io.BytesIO(png(16, 16, (40, 90, 200))), width=Inches(0.3))
    para.add_run(" and then more text.")
    pic = d.inline_shapes[0]._inline.docPr
    pic.set("descr", "An orange square")
    d.add_paragraph("A small image, a third of the page:")
    d.add_picture(io.BytesIO(png(32, 32, (40, 160, 90))), width=Inches(2))
    table = d.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "In a table:"
    table.cell(0, 1).paragraphs[0].add_run().add_picture(io.BytesIO(png(16, 16, (120, 40, 160))), width=Inches(1))
    return save(d, "images")


def word_fields_and_symbols() -> bytes:
    d = new("Fields, links and symbols")
    rel = d.part.relate_to(
        "https://example.com/route", docx.opc.constants.RELATIONSHIP_TYPE.HYPERLINK, is_external=True
    )
    js = d.part.relate_to("javascript:alert(1)", docx.opc.constants.RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    add(
        d,
        f'<w:p><w:r><w:t xml:space="preserve">See </w:t></w:r><w:hyperlink r:id="{rel}"><w:r><w:t>the route</w:t></w:r>'
        f'</w:hyperlink><w:r><w:t xml:space="preserve">, not </w:t></w:r><w:hyperlink r:id="{js}">'
        "<w:r><w:t>this</w:t></w:r>"
        "</w:hyperlink><w:r><w:t>.</w:t></w:r></w:p>"
        # A link to a bookmark on a paragraph that is no heading, as a cross-reference makes one.
        '<w:p><w:r><w:t xml:space="preserve">Details are </w:t></w:r><w:hyperlink w:anchor="_Ref100">'
        "<w:r><w:t>below</w:t></w:r></w:hyperlink><w:r><w:t>.</w:t></w:r></w:p>"
        # A simple field and a complex one, as Word keeps a date it filled in.
        '<w:p><w:r><w:t xml:space="preserve">Printed on </w:t></w:r>'
        '<w:fldSimple w:instr=" DATE \\@ &quot;d MMMM yyyy&quot; ">'
        '<w:r><w:t>1 October 2026</w:t></w:r></w:fldSimple><w:r><w:t xml:space="preserve">, page </w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r><w:r>'
        '<w:fldChar w:fldCharType="end"/></w:r>'
        "<w:r><w:t>.</w:t></w:r></w:p>"
        # Symbols, a hyphen that doesn't break, a soft hyphen, a tab, a line break and a page break.
        '<w:p><w:r><w:t xml:space="preserve">Tick </w:t></w:r><w:r><w:sym w:font="Wingdings" w:char="F0FC"/></w:r>'
        '<w:r><w:t xml:space="preserve"> e</w:t></w:r><w:r><w:noBreakHyphen/></w:r><w:r><w:t>mail, book</w:t></w:r>'
        "<w:r><w:softHyphen/></w:r><w:r><w:t>keeping</w:t></w:r><w:r><w:tab/></w:r><w:r><w:t>after a tab</w:t></w:r>"
        '<w:r><w:br/></w:r><w:r><w:t>after a break</w:t></w:r><w:r><w:br w:type="page"/></w:r>'
        "<w:r><w:t>after a page break</w:t></w:r></w:p>"
        # Content controls: a date picker and a checkbox, as forms have them; a smart tag.
        '<w:p><w:r><w:t xml:space="preserve">Date: </w:t></w:r><w:sdt><w:sdtPr>'
        '<w:date w:fullDate="2026-10-01T00:00:00Z">'
        '<w:dateFormat w:val="d-M-yyyy"/></w:date></w:sdtPr><w:sdtContent><w:r><w:t>1-10-2026</w:t>'
        "</w:r></w:sdtContent></w:sdt>"
        '<w:r><w:t xml:space="preserve"> Done: </w:t></w:r><w:sdt><w:sdtPr><w14:checkbox><w14:checked w14:val="1"/>'
        "</w14:checkbox></w:sdtPr><w:sdtContent><w:r><w:t>☒</w:t></w:r></w:sdtContent></w:sdt>"
        '<w:r><w:t xml:space="preserve"> In </w:t></w:r><w:smartTag w:uri="urn:schemas-microsoft-com:office:smarttags" '
        'w:element="City"><w:r><w:t>Utrecht</w:t></w:r></w:smartTag><w:r><w:t>.</w:t></w:r></w:p>'
        '<w:p><w:bookmarkStart w:id="100" w:name="_Ref100"/><w:r><w:t>The details: two days.</w:t></w:r>'
        '<w:bookmarkEnd w:id="100"/></w:p>',
    )
    return save(d, "fields-and-symbols")


def word_scripts_and_emoji() -> bytes:
    d = new("Scripts and emoji")
    d.add_paragraph("Emoji: 🚲 🌧️ 👩🏽‍💻 and flags 🇳🇱.")
    add(
        d,
        '<w:p><w:pPr><w:bidi/><w:jc w:val="right"/></w:pPr><w:r><w:rPr><w:rtl/></w:rPr>'
        "<w:t>مرحبا بالعالم</w:t></w:r></w:p>"
        "<w:p><w:pPr><w:bidi/></w:pPr><w:r><w:rPr><w:rtl/></w:rPr><w:t>שלום עולם</w:t></w:r></w:p>",
    )
    d.add_paragraph("日本語のテキスト、そして中文。")
    d.add_paragraph("Combining: été; zero​width; non breaking.")
    d.add_paragraph("Ligatures ﬁ and ﬂ, math 𝑥² + 𝑦².")
    return save(d, "scripts-and-emoji")


def word_headings_out_of_order() -> bytes:
    d = docx.Document()  # no Title paragraph: the first heading is the title
    d.add_heading("A heading as the title", level=1)
    d.add_paragraph("Intro.")
    d.add_heading("Skipped to four", level=4)
    d.add_paragraph("Under it.")
    d.add_heading("", level=2)  # an empty heading
    d.add_heading("Two", level=2)
    para = d.add_paragraph()
    para.add_run("A bold line as a heading").bold = True
    d.add_paragraph("Text under the bold line.")
    d.add_heading("Notes", level=2)
    d.add_heading("Notes", level=2)
    d.add_paragraph("Twice the same heading.")
    return save(d, "headings-out-of-order")


def word_empty() -> bytes:
    return save(docx.Document(), "empty")


def word_only_whitespace() -> bytes:
    d = docx.Document()
    for text in ("", " ", "\t", " "):
        d.add_paragraph(text)
    d.add_page_break()
    return save(d, "only-whitespace")


def word_sample() -> bytes:
    """A document of everything people write, for the programs to open and save their own way
    (scripts/office_corpus.py)."""
    d = new("A weekend in Utrecht")
    d.add_paragraph("Notes by Jan Jansen", style="Subtitle")
    d.add_heading("Getting there", level=1)
    para = d.add_paragraph("We took the ")
    para.add_run("early").bold = True
    para.add_run(" train; the ")
    para.add_run("timetable").italic = True
    para.add_run(" is on ")
    rel = d.part.relate_to(
        "https://example.com/trains", docx.opc.constants.RELATIONSHIP_TYPE.HYPERLINK, is_external=True
    )
    add(
        d,
        f'<w:p><w:hyperlink r:id="{rel}"><w:r><w:rPr><w:rStyle w:val="Hyperlink"/></w:rPr>'
        "<w:t>example.com</w:t></w:r></w:hyperlink></w:p>",
    )
    d.add_paragraph("What to bring:")
    d.add_paragraph("A coat", style="List Bullet")
    d.add_paragraph("Boots", style="List Bullet 2")
    d.add_paragraph("A map", style="List Bullet")
    d.add_heading("The days", level=1)
    d.add_heading("Saturday", level=2)
    d.add_paragraph("First the canal,", style="List Number")
    d.add_paragraph("then the tower.", style="List Number")
    d.add_paragraph("It rained, then it didn't.", style="Quote")
    t = d.add_table(rows=3, cols=2)
    t.style = d.styles["Table Grid"]
    for r, (a, b) in enumerate([("What", "Cost"), ("Train", "€ 24"), ("Lunch", "€ 12")]):
        t.cell(r, 0).paragraphs[0].add_run(a).bold = r == 0
        t.cell(r, 1).paragraphs[0].add_run(b).bold = r == 0
    d.add_picture(io.BytesIO(png(48, 32, (60, 120, 180))), width=Inches(3))
    d.inline_shapes[0]._inline.docPr.set("descr", "A blue rectangle")
    para = d.add_paragraph("Back home at ")
    run = para.add_run("eight")
    para.add_run(".")
    d.add_comment(run, text="Or nine?", author="Jan Jansen", initials="JJ")
    code = d.add_paragraph().add_run("FOLDER_URL")
    code.font.name = "Courier New"
    return save(d, "sample")


WORD = {
    "tracked-changes": word_tracked_changes,
    "comments": word_comments,
    "text-box": word_text_box,
    "header-footer": word_header_footer,
    "numbering-without-list-style": word_numbering_without_list_style,
    "dutch-styles": word_dutch_styles,
    "table-of-contents": word_table_of_contents,
    "footnotes": word_footnotes,
    "tables": word_tables,
    "images": word_images,
    "fields-and-symbols": word_fields_and_symbols,
    "scripts-and-emoji": word_scripts_and_emoji,
    "headings-out-of-order": word_headings_out_of_order,
    "empty": word_empty,
    "only-whitespace": word_only_whitespace,
}


# --- Markdown no editor writes ---


def markdown_garbage() -> bytes:
    rng = random.Random(20261025)
    return bytes(rng.randrange(256) for _ in range(2048))


def markdown_markup_soup() -> bytes:
    rng = random.Random(1843)
    pieces = ["#", "*", "_", "`", "[", "]", "(", ")", "!", ">", "-", "|", "~", "<", "&", "\n", " ", "word", "1."]
    return "".join(rng.choice(pieces) for _ in range(1500)).encode()


def markdown_utf16() -> bytes:
    text = "# Notities\r\n\r\nGeschreven in Kladblok, opgeslagen als Unicode: café, één.\r\n"
    return "﻿".encode("utf-16-le") + text.encode("utf-16-le")


def markdown_windows_1252() -> bytes:
    return "# Café\n\nOpgeslagen in Windows-1252: één, ‘quotes’, € 5 – genoeg.\n".encode("cp1252")


MARKDOWN = {
    "garbage": markdown_garbage,
    "markup-soup": markdown_markup_soup,
    "utf-16": markdown_utf16,
    "windows-1252": markdown_windows_1252,
}


# The images the hand-written Markdown cases show, in their folders as the editors keep them.
IMAGES = {
    "markdown/nextcloud-text/.attachments.4821/foto.png": (40, 30, (200, 120, 40)),
    "markdown/obsidian/kanaal.png": (40, 30, (40, 120, 200)),
    "markdown/raw-html/logo.png": (16, 16, (90, 90, 90)),
    "markdown/commonmark-corners/picture.png": (16, 16, (150, 40, 90)),
}


def main() -> None:
    only = set(sys.argv[1:])
    for path, (width, height, color) in IMAGES.items():
        (CORPUS / path).parent.mkdir(parents=True, exist_ok=True)
        (CORPUS / path).write_bytes(png(width, height, color))
    for kind, cases, name in (("word", WORD, "input.docx"), ("markdown", MARKDOWN, "input.md")):
        for case, make in cases.items():
            if only and case not in only:
                continue
            folder = CORPUS / kind / case
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(make())
            print(f"{kind}/{case}")


if __name__ == "__main__":
    main()
