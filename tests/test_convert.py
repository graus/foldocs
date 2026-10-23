from xml.etree import ElementTree

import pytest

from foldocs import convert


def image(src):
    return {
        "src": src,
        "base": "/media/x/",
        "width": 10,
        "height": 10,
        "color": "#000",
        "variants": [{"w": 10, "file": "a.webp"}],
    }


def attrs(info, sizes=None):
    return {"src": info["base"] + "a.webp", "width": 10, "height": 10} | ({"sizes": sizes} if sizes else {})


def convert_body(body):
    return convert.convert(f"<html><body>{body}</body></html>", image, attrs)


def test_title_subtitle_and_inline_styles():
    doc = convert_body(
        '<p class="title"><span>My title</span></p><p class="subtitle"><span>Sub</span></p>'
        '<p><span>A </span><span style="font-weight:700">bold</span><span style="font-style:italic">it</span></p>'
    )
    assert doc.title == "My title"
    assert doc.subtitle == "Sub"
    assert doc.html == "<p>A <strong>bold</strong><em>it</em></p>"


def test_headings_shift_down_and_internal_links_follow():
    doc = convert_body('<p>Hi</p><h1 id="h.abc"><span>Intro</span></h1><p><a href="#h.abc">to intro</a></p>')
    assert doc.html == '<p>Hi</p>\n<h2 id="intro">Intro</h2>\n<p><a href="#intro">to intro</a></p>'


def test_google_redirects_are_unwrapped_and_comments_dropped():
    doc = convert_body(
        '<p><span><a href="https://www.google.com/url?q=https://example.org/&amp;sa=D">x</a></span>'
        '<sup><a href="#cmnt1" id="cmnt_ref1">[a]</a></sup></p>'
        '<div><p><a href="#cmnt_ref1" id="cmnt1">[a]</a><span>a comment</span></p></div>'
    )
    assert doc.html == '<p><a href="https://example.org/">x</a></p>'


def test_flat_lists_are_nested_by_level():
    doc = convert_body(
        '<ul class="lst-kix_a-0 start"><li><span>one</span></li></ul>'
        '<ul class="lst-kix_a-1 start"><li><span>one.a</span></li></ul>'
        '<ul class="lst-kix_a-0"><li><span>two</span></li></ul>'
    )
    assert doc.html == "<ul><li>one<ul><li>one.a</li></ul></li><li>two</li></ul>"


def test_images_become_figures_and_empty_paragraphs_vanish():
    doc = convert_body('<p><span></span></p><p><span><img src="images/image1.png" alt="A cat"></span></p>')
    img = '<img alt="A cat" decoding="async" height="10" loading="lazy" src="/media/x/a.webp" width="10"/>'
    assert doc.html == f"<figure>{img}</figure>"
    assert len(doc.images) == 1


def test_an_opening_heading_is_the_title_without_a_title_paragraph():
    doc = convert_body("<h1><span>Name</span></h1><h2><span>Role</span></h2><p><span>Bio</span></p>")
    assert doc.title == "Name"
    assert doc.html == '<h2 id="role">Role</h2>\n<p>Bio</p>'


def test_an_indented_paragraph_is_a_quote():
    doc = convert_body(
        '<p>Intro</p><p style="margin-left:36pt"><span>Quoted.</span></p><p style="margin-left:0"><span>Not.</span></p>'
    )
    assert doc.html == "<p>Intro</p>\n<blockquote>Quoted.</blockquote>\n<p>Not.</p>"


def test_a_word_file_takes_the_same_road(tmp_path):
    import docx as python_docx

    d = python_docx.Document()
    d.add_paragraph("Titel", style="Title")
    d.add_paragraph("Ondertitel", style="Subtitle")
    d.add_heading("Kop", level=1)
    d.add_paragraph("Een **punt**", style="List Bullet")
    d.add_paragraph("Geciteerd.", style="Quote")
    path = tmp_path / "t.docx"
    d.save(path)
    doc = convert.convert(convert.word_html(path.read_bytes()), image, attrs)
    assert (doc.title, doc.subtitle) == ("Titel", "Ondertitel")
    assert doc.html == '<h2 id="kop">Kop</h2>\n<ul><li>Een **punt**</li></ul>\n<blockquote>Geciteerd.</blockquote>'


def test_only_safe_links_survive():
    doc = convert_body(
        '<p><a href="javascript:alert(1)">bad</a> <a href="mailto:a@b.nl">mail</a> <a href="#h.x">here</a></p>'
    )
    assert doc.html == '<p>bad <a href="mailto:a@b.nl">mail</a> <a href="#">here</a></p>'


def test_an_image_keeps_its_share_of_the_page_width():
    page = '<html><body style="max-width: 432pt; padding: 72pt">{}</body></html>'  # 576px of text
    small = convert.convert(
        page.format('<p><img src="a.png" style="width: 288.00px; height: 100px"></p>'), image, attrs
    )
    full = convert.convert(page.format('<p><img src="a.png" style="width: 566.93px; height: 300px"></p>'), image, attrs)
    assert small.html.startswith('<figure style="--width: 50%">')
    assert 'sizes="(min-width: 48rem) 21.2rem, calc((100vw - 2rem) * 0.50)"' in small.html  # half the file, too
    assert full.html.startswith("<figure>") and "sizes" not in full.html  # (nearly) the page's width: the column's


def test_text_keeps_punctuation_next_to_emphasis():
    doc = convert_body('<p>The folder <span style="font-style:italic">site</span>, then <b>more</b>.</p>')
    assert doc.text == "The folder site, then more."


def test_a_word_tables_header_row_is_kept(tmp_path):
    from docx import Document

    d = Document()
    table = d.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Setting", "Light"
    table.cell(1, 0).text, table.cell(1, 1).text = "Accent", "#2156c4"
    header = table.rows[0]._tr.get_or_add_trPr()
    header.append(header.makeelement("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tblHeader", {}))
    path = tmp_path / "t.docx"
    d.save(path)
    doc = convert.convert(convert.word_html(path.read_bytes()), image, attrs)
    assert "Setting" in doc.html and "Accent" in doc.html


def test_a_list_inside_a_list_item_stays_nested():
    """How Word files (through mammoth) nest lists: the inner list inside the item."""
    doc = convert_body("<ul><li>Folder<ul><li>Doc<ol><li>One</li></ol></li></ul></li><li>Next</li></ul>")
    assert doc.html == "<ul><li>Folder<ul><li>Doc<ol><li>One</li></ol></li></ul></li><li>Next</li></ul>"


def test_text_in_a_monospace_font_is_code(tmp_path):
    doc = convert_body(
        '<p><span>Put </span><span style="font-family:&quot;Courier New&quot;;font-weight:400">home</span>'
        '<span> in </span><span style="font-family:&quot;Roboto Mono&quot;">site</span>'
        '<span style="font-family:&quot;Arial&quot;">, then wait.</span></p>'
    )
    assert doc.html == "<p>Put <code>home</code> in <code>site</code>, then wait.</p>"

    import docx as python_docx

    d = python_docx.Document()
    p = d.add_paragraph("Put ")
    p.add_run("home").font.name = "Consolas"
    p.add_run(" in ")
    p.add_run("site").font.name = "Courier New"
    p.add_run(", ")
    p.add_run("then").bold = True
    p.add_run(" ")
    p.add_run("wait").italic = True
    p.add_run(".")
    path = tmp_path / "t.docx"
    d.save(path)
    assert convert.convert(convert.word_html(path.read_bytes()), image, attrs).html == (
        "<p>Put <code>home</code> in <code>site</code>, <strong>then</strong> <em>wait</em>.</p>"
    )


def test_only_monospace_fonts_are_code():
    for font in ["Roboto Mono", '"Source Code Pro"', "Courier New", "'Consolas', monospace", "VT323"]:
        assert convert._monospace(font), font
    for font in ["Monotype Corsiva", "Monoton", "Montserrat", "Arial", "Cascadia Mono Sans"]:
        assert not convert._monospace(font), font


def test_a_word_files_own_fixed_pitch_fonts_are_code(tmp_path):
    """Word, LibreOffice and Collabora say in the file which fonts are monospace."""
    import zipfile

    import docx as python_docx

    d = python_docx.Document()
    p = d.add_paragraph("Set ")
    p.add_run("FOLDER_URL").font.name = "Typewriter Deluxe"  # in no list
    p.add_run(" once.")
    path = tmp_path / "t.docx"
    d.save(path)
    with zipfile.ZipFile(path) as z:
        files = {name: z.read(name) for name in z.namelist()}
    table = files["word/fontTable.xml"].decode()
    font = '<w:font w:name="Typewriter Deluxe"><w:family w:val="modern"/><w:pitch w:val="fixed"/></w:font>'
    files["word/fontTable.xml"] = table.replace("</w:fonts>", font + "</w:fonts>").encode()
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    html = convert.convert(convert.word_html(path.read_bytes()), image, attrs).html
    assert html == "<p>Set <code>FOLDER_URL</code> once.</p>"


def _word_file_with(tmp_path, part, xml, document="word/document.xml"):
    import zipfile

    import docx as python_docx

    d = python_docx.Document()
    d.add_paragraph("Text")
    path = tmp_path / "t.docx"
    d.save(path)
    with zipfile.ZipFile(path) as z:
        files = {name: z.read(name) for name in z.namelist()}
    files[part] = xml
    files["_rels/.rels"] = files["_rels/.rels"].replace(b"word/document.xml", document.encode())
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path.read_bytes()


DTD = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "EXPANDED">]><x>&a;</x>'


@pytest.mark.parametrize("part", ["word/document.xml", "word/fontTable.xml", "word/_rels/document.xml.rels"])
def test_a_word_file_with_a_dtd_anywhere_is_refused(tmp_path, part):
    """A Word file is untrusted: entities in its XML are refused before they're expanded."""
    data = _word_file_with(tmp_path, part, DTD)
    with pytest.raises(ElementTree.ParseError):
        convert.word_html(data)
    if part == "word/fontTable.xml":
        assert convert._fixed_pitch_fonts(data) == frozenset()


def test_a_word_file_whose_document_has_another_name_is_checked_too(tmp_path):
    """The file itself says which part is the document: its name proves nothing."""
    data = _word_file_with(tmp_path, "word/doc.dat", DTD, document="word/doc.dat")
    with pytest.raises(ElementTree.ParseError):
        convert.word_html(data)


def test_an_svg_naming_its_standard_dtd_is_fine_but_one_declaring_entities_is_not(tmp_path):
    """Word keeps an SVG's original in the file; Illustrator's name the SVG DTD, which nothing reads."""
    svg = (
        b'<?xml version="1.0"?><!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
        b'"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd"><svg xmlns="http://www.w3.org/2000/svg"/>'
    )
    assert "Text" in convert.word_html(_word_file_with(tmp_path, "word/media/image1.svg", svg))
    with pytest.raises(ElementTree.ParseError):
        convert.word_html(_word_file_with(tmp_path, "word/media/image1.svg", DTD))


def test_two_headings_with_the_same_text_get_ids_of_their_own():
    html = convert_body("<p>Intro.</p><h2>Notes</h2><p>a</p><h2>Notes</h2><p>b</p>").html
    assert 'id="notes"' in html and 'id="notes-2"' in html


def test_a_documents_links_are_listed_in_order():
    doc = convert_body('<p><a href="mailto:a@b.org">mail</a> and <a href="https://x.com/ann">x</a></p>')
    assert doc.links == ["mailto:a@b.org", "https://x.com/ann"]


def test_the_first_paragraph_heading_and_list_are_recorded_as_text():
    doc = convert_body("<p>Hello <b>there</b>.</p><h1>Topics</h1><ul><li>One</li><li>Two</li></ul><p>More.</p>")
    assert doc.first == {"paragraph": "Hello there.", "heading": "Topics", "list": ["One", "Two"]}


def test_a_tables_bold_first_row_is_its_header():
    bold = '<td><p><span style="font-weight:700">{}</span></p></td>'
    row = "<tr><td><p>{}</p></td><td><p>{}</p></td></tr>"
    html = convert_body(f"<table><tr>{bold.format('In')}{bold.format('Out')}</tr>{row.format('a', 'b')}</table>").html
    assert '<thead><tr><th scope="col">In</th><th scope="col">Out</th></tr></thead>' in html
    assert "<tbody><tr><td>a</td><td>b</td></tr></tbody>" in html
    plain = convert_body(f"<table>{row.format('a', 'b')}{row.format('c', 'd')}</table>").html
    assert "<thead>" not in plain and plain.count("<tr>") == 2


def test_a_heading_never_takes_an_id_the_page_already_has():
    assert 'id="main-2"' in convert_body("<p>Intro.</p><h2>Main</h2><p>a</p>").html
