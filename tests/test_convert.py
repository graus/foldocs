import io
from xml.etree import ElementTree

import pytest

from foldocs import convert
from foldocs.formats import Reading, fonts, gdoc, markdown, word
from foldocs.sources import DocExport, Entry


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


def convert_doc(html):
    """A Docs export, read as its format does, then made a page."""
    text = gdoc.clean(DocExport(html), fetch=None)
    return convert.convert(text.body, image, attrs, text.title, text.subtitle)


def convert_body(body):
    return convert_doc(f"<html><body>{body}</body></html>")


def convert_word(data):
    text = word.clean(word.word_html(data))
    return convert.convert(text.body, image, attrs, text.title, text.subtitle)


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
    doc = convert_word(path.read_bytes())
    assert (doc.title, doc.subtitle) == ("Titel", "Ondertitel")
    assert doc.html == '<h2 id="kop">Kop</h2>\n<ul><li>Een **punt**</li></ul>\n<blockquote>Geciteerd.</blockquote>'


def test_only_safe_links_survive():
    doc = convert_body(
        '<p><a href="javascript:alert(1)">bad</a> <a href="mailto:a@b.nl">mail</a> <a href="#h.x">here</a></p>'
    )
    assert doc.html == '<p>bad <a href="mailto:a@b.nl">mail</a> <a href="#">here</a></p>'


def test_an_image_keeps_its_share_of_the_page_width():
    page = '<html><body style="max-width: 432pt; padding: 72pt">{}</body></html>'  # 576px of text
    small = convert_doc(page.format('<p><img src="a.png" style="width: 288.00px; height: 100px"></p>'))
    full = convert_doc(page.format('<p><img src="a.png" style="width: 566.93px; height: 300px"></p>'))
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
    doc = convert_word(path.read_bytes())
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
    assert convert_word(path.read_bytes()).html == (
        "<p>Put <code>home</code> in <code>site</code>, <strong>then</strong> <em>wait</em>.</p>"
    )


def test_only_monospace_fonts_are_code():
    for font in ["Roboto Mono", '"Source Code Pro"', "Courier New", "'Consolas', monospace", "VT323"]:
        assert fonts.monospace(font), font
    for font in ["Monotype Corsiva", "Monoton", "Montserrat", "Arial", "Cascadia Mono Sans"]:
        assert not fonts.monospace(font), font


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
    html = convert_word(path.read_bytes()).html
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
        word.word_html(data)
    if part == "word/fontTable.xml":
        assert word._parts(data, part) == [None]


def test_a_word_file_whose_document_has_another_name_is_checked_too(tmp_path):
    """The file itself says which part is the document: its name proves nothing."""
    data = _word_file_with(tmp_path, "word/doc.dat", DTD, document="word/doc.dat")
    with pytest.raises(ElementTree.ParseError):
        word.word_html(data)


def test_an_svg_naming_its_standard_dtd_is_fine_but_one_declaring_entities_is_not(tmp_path):
    """Word keeps an SVG's original in the file; Illustrator's name the SVG DTD, which nothing reads."""
    svg = (
        b'<?xml version="1.0"?><!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
        b'"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd"><svg xmlns="http://www.w3.org/2000/svg"/>'
    )
    assert "Text" in word.word_html(_word_file_with(tmp_path, "word/media/image1.svg", svg))
    with pytest.raises(ElementTree.ParseError):
        word.word_html(_word_file_with(tmp_path, "word/media/image1.svg", DTD))


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


class OneFile:
    def __init__(self, data):
        self.data = data

    def download(self, entry):
        return self.data


def convert_markdown(text):
    entry = Entry("m", "m.md", "text/markdown", [], "", "")
    read = markdown.read(Reading(entry, OneFile(text.encode())))
    return convert.convert(read.body, image, attrs, read.title, read.subtitle)


def test_a_markdown_file_takes_the_same_road_as_a_doc():
    doc = convert_markdown(
        "# A trip\n\nWe went **out** and saw ~~nothing~~ `code`.\n\n## Day one\n\n"
        "- one\n  - nested\n- two\n\n> A quote\n\n![The beach](beach.jpg)\n\n---\n"
    )
    assert doc.title == "A trip"
    assert doc.html == (
        "<p>We went <strong>out</strong> and saw <s>nothing</s> <code>code</code>.</p>\n"
        '<h2 id="day-one">Day one</h2>\n'
        "<ul><li>one<ul><li>nested</li></ul></li><li>two</li></ul>\n"
        "<blockquote>A quote</blockquote>\n"
        '<figure><img alt="The beach" decoding="async" height="10" loading="lazy" src="/media/x/a.webp" width="10"/>'
        "</figure>\n<hr/>"
    )
    assert [i["src"] for i in doc.images] == ["beach.jpg"]


def test_markdown_front_matter_gives_a_title_and_subtitle():
    doc = convert_markdown('---\ntitle: "From the top"\ndescription: A line\ntags: [a, b]\n---\n\n# Heading\n\nText.\n')
    assert (doc.title, doc.subtitle) == ("From the top", "A line")
    assert doc.html == '<h2 id="heading">Heading</h2>\n<p>Text.</p>'  # a title named: the heading stays


def test_markdown_tables_and_code_blocks_keep_their_shape():
    doc = convert_markdown("| Name | **Age** |\n|---|---|\n| Ann | 30 |\n\n```py\nif x:\n    y()\n```\n")
    assert doc.html == (
        '<div class="table-wrap"><table><thead><tr><th scope="col">Name</th><th scope="col">Age</th></tr></thead>'
        "<tbody><tr><td>Ann</td><td>30</td></tr></tbody></table></div>\n"
        "<pre><code>if x:\n    y()</code></pre>"
    )


def test_markdown_in_page_links_reach_their_headings():
    doc = convert_markdown("[Down](#day-one) [nowhere](#gone)\n\n## Day one\n")
    assert '<a href="#day-one">Down</a> <a href="#">nowhere</a>' in doc.html


def test_html_and_scripts_in_markdown_never_reach_the_site():
    doc = convert_markdown('<script>alert(1)</script>\n\n[click](javascript:alert(1)) <b onclick="x">b</b>\n')
    assert "<script" not in doc.html and "<b " not in doc.html and "href" not in doc.html
    assert "&lt;script&gt;" in doc.html  # shown as the text it is


def test_markdown_front_matter_saved_on_windows_is_read_too():
    doc = convert_markdown("---\r\ntitle: Hello\r\ntags:\r\n  - a\r\n---\r\nSome text.\r\n")
    assert (doc.title, doc.html) == ("Hello", "<p>Some text.</p>")


def test_a_markdown_file_opening_with_a_rule_keeps_its_text():
    doc = convert_markdown("---\n\nIntro paragraph\n\nHeading\n---\n\nBody\n")
    assert "Intro paragraph" in doc.html and "Body" in doc.html
    assert convert_markdown("---\n---\n\nText\n").html == "<hr/>\n<hr/>\n<p>Text</p>"  # two rules, no front matter


def test_a_numbered_list_goes_on_after_a_code_block():
    doc = convert_markdown("1. a\n2. b\n\n```\ncode\n```\n\n3. c\n")
    assert doc.html == '<ol><li>a</li><li>b</li></ol>\n<pre><code>code</code></pre>\n<ol start="3"><li>c</li></ol>'


def test_markdown_quotes_and_list_items_keep_their_paragraphs_and_code():
    doc = convert_markdown("> one\n>\n> two\n\n> alone\n\n- Step:\n\n  ```\n  x\n  y\n  ```\n- Next\n\n  - sub\n")
    assert doc.html == (
        "<blockquote><p>one</p><p>two</p></blockquote>\n"
        "<blockquote>alone</blockquote>\n"
        "<ul><li><p>Step:</p><pre><code>x\ny</code></pre></li><li>Next<ul><li>sub</li></ul></li></ul>"
    )


def test_text_beside_a_block_in_a_list_item_is_kept():
    """A tight Markdown list writes an item's text loose, before its code or quote."""
    doc = convert_markdown("- item one\n  ```\n  code\n  ```\n- item\n  > quoted\n")
    assert doc.html == (
        "<ul><li><p>item one</p><pre><code>code</code></pre></li>"
        "<li><p>item</p><blockquote>quoted</blockquote></li></ul>"
    )


def test_a_numbered_doc_list_goes_on_after_an_image():
    doc = convert_body(
        '<ol class="lst-kix_a-0 start" start="1"><li>one</li></ol><p>between</p>'
        '<ol class="lst-kix_a-0" start="2"><li>two</li></ol>'
    )
    assert doc.html == '<ol><li>one</li></ol>\n<p>between</p>\n<ol start="2"><li>two</li></ol>'


def test_a_word_table_of_contents_leads_to_its_headings():
    """mammoth writes Word's bookmark inside the heading it marks."""
    text = word.clean(
        '<p><a href="#_Toc1">Intro</a></p><h1><a id="_Toc1"></a><a id="_Ref2"></a>Intro</h1>'
        '<p><a href="#_Ref2">Up</a></p>'
    )
    doc = convert.convert(text.body, image, attrs, "Title")
    assert '<a href="#intro">Up</a>' in doc.html
    assert '<a href="#intro">Intro</a>' in doc.html and 'id="intro"' in doc.html


def test_headings_in_quotes_move_with_the_rest():
    doc = convert_markdown("# T\n\n### A\n\n> ### Q\n>\n> text\n")
    assert '<h2 id="a">A</h2>' in doc.html and '<h2 id="q">Q</h2>' in doc.html


@pytest.mark.parametrize(
    "front, expected",
    [
        ("title: Hi\ntags:\n- a\n- b", {"title": "Hi", "tags": None}),  # a list as Jekyll and Obsidian write it
        ("title: >\n  A long title\ndescription: |\n  more", {"title": None, "description": None}),
        ("title: 'Tis the season", {"title": "'Tis the season"}),
        ('title: He said "hi"', {"title": 'He said "hi"'}),
        ('title: "Quoted # not a comment"', {"title": "Quoted # not a comment"}),
        ("title: Jan # a draft", {"title": "Jan"}),
        ("title: C# tips", {"title": "C# tips"}),
    ],
)
def test_markdown_front_matter_values(front, expected):
    meta, text = markdown.front_matter(f"---\n{front}\n---\nBody\n")
    assert text == "Body\n"
    assert {key: meta.get(key) for key in expected} == expected


def test_a_list_item_of_an_image_alone_keeps_it():
    doc = convert_body('<ul><li>one</li><li><img src="a.png"></li></ul>')
    assert doc.html.startswith("<ul><li>one</li><li><figure>") and len(doc.images) == 1


def test_a_heading_taken_out_as_the_title_leaves_its_id_to_the_next():
    doc = convert_markdown("# Notes\n\nText.\n\n## Notes\n\n[up](#notes)\n")
    assert doc.title == "Notes" and '<h2 id="notes">Notes</h2>' in doc.html


def test_a_merged_cell_keeps_its_span():
    html = convert_body('<table><tr><td colspan="2">a</td></tr><tr><td rowspan="x">b</td><td>c</td></tr></table>').html
    assert '<td colspan="2">a</td>' in html and "<td>b</td>" in html


def test_a_line_break_ending_a_paragraph_is_left_out():
    assert convert_body("<p>Text<br><br></p><p><br>More</p>").html == "<p>Text</p>\n<p>More</p>"


@pytest.mark.parametrize(
    "text, meta, rest",
    [
        ('+++\ntitle = "Hugo"\ntags = ["a"]\n+++\nBody\n', {"title": "Hugo"}, "Body\n"),
        ("+++\nnot toml at all\n+++\nBody\n", {}, "+++\nnot toml at all\n+++\nBody\n"),
        ('{"title": "JSON", "n": 1}\nBody\n', {"title": "JSON"}, "Body\n"),
        ('{"title": "JSON"} and text\n', {}, '{"title": "JSON"} and text\n'),  # not on a line of its own
        ("{% raw %}\n", {}, "{% raw %}\n"),
        ("---\ntitle: Pandoc\n...\nBody\n", {"title": "Pandoc"}, "Body\n"),
    ],
)
def test_markdown_front_matter_in_toml_json_and_pandocs_yaml(text, meta, rest):
    assert markdown.front_matter(text) == (meta, rest)


def test_a_markdown_files_encoding_is_found():
    assert markdown.decode("café\r\n".encode("utf-16")) == "café\n"
    assert markdown.decode("café".encode("cp1252")) == "café"
    assert markdown.decode("﻿café".encode()) == "café"
    with pytest.raises(ValueError, match="not a text file"):
        markdown.decode(b"\x89PNG\r\n\x1a\n\x00\x00")


def test_a_link_or_style_split_into_runs_is_one_again():
    doc = convert_body(
        '<p><a href="https://example.org/a"> </a><a href="https://example.org/a">Research</a>'
        '<span style="font-weight:700">bo</span><span style="font-weight:700">ld</span></p>'
    )
    assert doc.html == '<p><a href="https://example.org/a"> Research</a><strong>bold</strong></p>'


def test_a_monospace_font_on_a_heading_a_link_or_punctuation_is_its_look_not_code():
    mono = 'style="font-family:&quot;Courier New&quot;"'
    doc = convert_body(
        f'<p>Intro.</p><h2><span {mono}>Latest</span></h2><p><span {mono}><a href="https://example.org/">More</a></span>'
        f"<span {mono}> · </span><span>set </span><span {mono}>FOLDER_URL</span></p>"
    )
    assert doc.html == (
        '<p>Intro.</p>\n<h2 id="latest">Latest</h2>\n'
        '<p><a href="https://example.org/">More</a> · set <code>FOLDER_URL</code></p>'
    )


def test_a_bookmark_with_no_id_it_can_keep_is_its_text():
    """An <a> without an address whose id is the themes', not a usable one, or none: its text."""
    for html in ['<p>x <a id="main">here</a></p>', '<p>x <a name="n">here</a></p>', '<p>x <a id="a b">here</a></p>']:
        doc = convert.convert(html, image, attrs, "T")
        assert doc.html == "<p>x here</p>", html


def test_two_line_breaks_are_a_blank_line_still():
    assert convert_body("<p>one<br><br>two</p>").html == "<p>one<br/><br/>two</p>"


def test_an_id_the_document_gives_is_kept_and_a_heading_makes_way():
    """A bookmark's or footnote's id is what links point at; a heading's is made up. Either order."""
    for html in [
        '<p><a href="#notes">go</a> <a id="notes"></a>mark</p><h2>Notes</h2>',
        '<h2>Notes</h2><p><a href="#notes">go</a> <a id="notes"></a>mark</p>',
    ]:
        doc = convert.convert(html, image, attrs, "T")
        assert '<h2 id="notes-2">Notes</h2>' in doc.html and '<a id="notes"></a>mark' in doc.html, html
        assert '<a href="#notes">go</a>' in doc.html
    footnotes = (
        '<h2>Footnote 1</h2><p>x<sup><a href="#footnote-1">[1]</a></sup></p><ol><li id="footnote-1">A note.</li></ol>'
    )
    doc = convert.convert(footnotes, image, attrs, "T")
    assert '<ol><li id="footnote-1">A note.</li></ol>' in doc.html and 'id="footnote-1-2"' in doc.html


def test_a_link_to_a_place_left_off_the_page_leads_nowhere():
    """A bookmark in the heading taken out as the title, or in a paragraph of an image alone."""
    doc = convert_body(
        '<h1><a id="top"></a>Title</h1><p><a id="bm"></a><img src="a.png"></p>'
        '<p><a href="#top">t</a> <a href="#bm">b</a></p>'
    )
    assert '<a href="#">t</a> <a href="#">b</a>' in doc.html


def test_a_digit_that_isnt_ascii_is_no_number():
    html = convert_body('<table><tr><td colspan="²">a</td></tr></table><ol start="³"><li>x</li></ol>').html
    assert "<td>a</td>" in html and "<ol><li>x</li></ol>" in html


def test_a_utf8_file_with_a_bad_byte_keeps_its_other_letters():
    assert markdown.decode("café — naïve".encode() + b"\xff") == "café — naïve�"


def test_markdown_after_an_html_tag_is_still_markdown():
    """As a README opens: only a comment is a block of HTML of its own."""
    doc = convert_markdown('<p align="center">\n![logo](logo.png) **bold**\n</p>\n\n<!-- hidden -->\n\nText.\n')
    assert "<strong>bold</strong>" in doc.html and len(doc.images) == 1 and "hidden" not in doc.html


def test_a_word_file_that_wont_unpack_says_so(tmp_path):
    import zipfile

    data = bytearray(_word_file_with(tmp_path, "word/extra.xml", b"<x>" + b"text " * 200 + b"</x>"))
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as z:
        info = z.getinfo("word/extra.xml")
    start = info.header_offset + 30 + len(info.filename) + len(info.extra)
    data[start : start + 8] = b"\xff" * 8  # its packed bytes broken
    with pytest.raises(ValueError, match="can be read"):
        word.word_html(bytes(data))


def test_code_of_punctuation_stays_code():
    mono = 'style="font-family:&quot;Courier New&quot;"'
    doc = convert_body(
        f"<p>Use <span {mono}>-&gt;</span> or <span {mono}>{{}}</span>.</p><p><span {mono}>}}</span></p>"
    )
    assert doc.html == "<p>Use <code>-&gt;</code> or <code>{}</code>.</p>\n<p><code>}</code></p>"


def test_markdown_after_a_comment_on_its_line_is_markdown():
    assert (
        convert_markdown("Intro.\n\n<!--more--> **Read on**\n").html == "<p>Intro.</p>\n<p><strong>Read on</strong></p>"
    )


def test_windows_1252_with_curly_quotes_is_not_taken_for_utf8():
    assert markdown.decode("café’s naïve été".encode("cp1252")) == "café’s naïve été"


def test_front_matter_nested_past_pythons_depth_is_text():
    for text in ['{"a":' + "[" * 100_000, "+++\na = " + "[" * 100_000 + "\n+++\nBody\n"]:
        assert markdown.front_matter(text) == ({}, text)


def test_only_a_page_number_ends_a_table_of_contents_entry():
    text = word.clean('<p><a href="#_Toc1">Intro\t12</a> <a href="#_Toc2">Preface\tiv</a> <a href="#x">Go\tmid</a></p>')
    assert [a.get_text() for a in text.body.find_all("a")] == ["Intro", "Preface", "Go\tmid"]
