"""Real documents, in the states people leave them in, read the way the sync reads them.

Each case in tests/corpus/<format>/<case>/ is a folder: the document (input.docx, input.md, or a
Google Docs export: export.zip, or export.html when the Doc was too large to zip), any files it
shows, and expected.json, the page convert makes of it. A change in what a case gives fails here
with a diff; when the change is meant, write the new pages and review them as a diff:

    UPDATE_CORPUS=1 uv run pytest tests/test_corpus.py

A case can have a case.toml, written by hand: what it shows (`about`), the words it is known to
lose and why (`lost`, `why`), and the encoding its text is really in (`encoding`), for the check
that no word is lost.

Every case, and variants of it with tags and attributes dropped, repeated, swapped and cut short,
must also hold to what every page holds to (check_page). Then all of them are synced together and
built in every bundled theme.
"""

import copy
import dataclasses
import difflib
import hashlib
import io
import json
import os
import random
import re
import struct
import tomllib
import zipfile
import zlib
from pathlib import Path
from xml.etree import ElementTree

import pytest
from bs4 import BeautifulSoup, Tag

from foldocs import cache, config, convert, render, site, sync
from foldocs import theme as themes
from foldocs.formats import DOC, Reading, format_for, markdown
from foldocs.sources import DocExport, Entry
from foldocs.sources.drive import unzip
from foldocs.xmlsafe import untrusted_xml

CORPUS = Path(__file__).parent / "corpus"
INPUTS = ("input.docx", "input.md", "export.zip", "export.html")
NOT_IN_THE_FOLDER = {"expected.json", "case.toml"}
UPDATE = bool(os.environ.get("UPDATE_CORPUS"))
THEMES = ("default", "journal", "index")
DATE = "2026-01-01T00:00:00Z"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def png() -> bytes:
    """A small image, for one a Docs export links to rather than bundles."""
    row = b"\x00" + b"\x80\x40\x20" * 8

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(row * 8)) + chunk(b"IEND", b"")


class Case:
    def __init__(self, folder: Path):
        self.folder = folder
        self.name = f"{folder.parent.name}/{folder.name}"
        self.input = next(folder / name for name in INPUTS if (folder / name).exists())
        about = folder / "case.toml"
        self.about = tomllib.loads(about.read_text()) if about.exists() else {}

    def files(self) -> dict[tuple[str, ...], Path]:
        """The case's folder as the site's folder has it: every file by its path in it."""
        return {
            path.relative_to(self.folder).parts: path
            for path in sorted(self.folder.rglob("*"))
            if path.is_file() and path.name not in NOT_IN_THE_FOLDER
        }


CASES = [Case(d) for d in sorted(CORPUS.glob("*/*")) if any((d / name).exists() for name in INPUTS)]


def file_id(where: tuple[str, ...]) -> str:
    return hashlib.sha1("/".join(where).encode()).hexdigest()[:16]


class Corpus:
    """A folder of cases, as a source: each case's files in a folder of its own (`prefix`), a Docs
    export as the Doc it was exported from."""

    kind = "drive"

    def __init__(self, cases: list[Case], prefix=lambda case: ()):
        self.entries, self.paths, self.exports = [], {}, {}
        for case in cases:
            for where, path in case.files().items():
                where = (*prefix(case), *where)
                if path.name.startswith("export.") and len(where) == len(prefix(case)) + 1:
                    entry = Entry(file_id(where), "input", DOC, list(where[:-1]), DATE, DATE)
                    self.exports[entry.id] = path
                elif found := format_for(_mime(path.name), path.name):
                    entry = Entry(file_id(where), where[-1], _mime(path.name), list(where[:-1]), DATE, DATE)
                    entry.format = found.name
                else:
                    continue  # images bundled with an export, files the site doesn't publish
                self.entries.append(entry)
                self.paths[entry.id] = path

    def walk(self):
        return self.entries, []

    def download(self, entry):
        return self.paths[entry.id].read_bytes()

    def export_doc(self, entry):
        path = self.exports[entry.id]
        if path.suffix == ".html":  # too large to zip: its images by URL
            return DocExport(path.read_text())
        return unzip(path.read_bytes())

    def fetch(self, url):
        if not url.startswith("https://lh3.googleusercontent.com/"):
            raise ValueError(f"Not fetching {url}")
        return png()

    def tree(self):
        return {e.where: e for e in self.entries}


def _mime(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower()
    types = {"md": "text/markdown", "docx": "application/octet-stream", "png": "image/png", "jpg": "image/jpeg"}
    return types.get(ext, "application/octet-stream" if ext in ("gif", "webp") else "")


def main_entry(source: Corpus, case: Case) -> Entry:
    return next(e for e in source.entries if source.paths[e.id] == case.input)


# Reading a document as the sync does, with images as a short name for what they are.


def image_info(data: bytes) -> dict:
    return {"src": f"image-{hashlib.sha1(data).hexdigest()[:8]}", "width": 8, "height": 8}


def image_attrs(info, sizes=None):
    return {"src": info["src"], "width": info["width"], "height": info["height"]} | ({"sizes": sizes} if sizes else {})


def page(case: Case, data: bytes | None = None) -> convert.Doc:
    """The case's page; with `data`, of that document instead of the case's own (a variant)."""
    source = Corpus([case])
    entry = main_entry(source, case)
    if data is not None:
        if entry.id in source.exports:
            source.export_doc = lambda _: DocExport(data.decode()) if data[:2] != b"PK" else unzip(data)
        else:
            source.download = lambda e, real=source.download: data if e is entry else real(e)
    text = entry.reader.read(Reading(entry, source, source.tree()))

    def image(src):
        try:
            return image_info(text.image(src))
        except Exception:  # as the sync leaves it out
            return None

    return convert.convert(text.body, image, image_attrs, text.title, text.subtitle)


def expected(case: Case) -> dict:
    """What the case gives: its page, or the error that says why there is none."""
    try:
        doc = page(case)
    except ValueError as e:
        return {"error": str(e)}
    return {
        "title": doc.title,
        "subtitle": doc.subtitle,
        "html": doc.html.split("\n"),
        "text": doc.text,
        "links": doc.links,
    }


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_a_case_gives_the_page_it_gave(case):
    found, path = expected(case), case.folder / "expected.json"
    if UPDATE or not path.exists():
        path.write_text(json.dumps(found, indent=1, ensure_ascii=False) + "\n")
        if not UPDATE:
            pytest.fail(f"{case.name}: no expected.json yet; written, review it")
        return
    stored = json.loads(path.read_text())
    if found != stored:
        diff = difflib.unified_diff(
            json.dumps(stored, indent=1, ensure_ascii=False).splitlines(),
            json.dumps(found, indent=1, ensure_ascii=False).splitlines(),
            "expected.json",
            "now",
            lineterm="",
        )
        pytest.fail(f"{case.name} gives another page (UPDATE_CORPUS=1 to accept):\n" + "\n".join(diff))


# What every page holds to.

ALLOWED = {
    **dict.fromkeys(["p", "ul", "table", "thead", "tbody", "tr", "blockquote", "pre", "hr"], set()),
    **dict.fromkeys(["strong", "em", "code", "s", "sup", "sub", "br"], set()),
    **dict.fromkeys(["h2", "h3", "h4", "h5", "h6", "li"], {"id"}),
    "ol": {"start"},
    "td": {"colspan", "rowspan"},
    "th": {"scope", "colspan", "rowspan"},
    "div": {"class"},
    "a": {"href", "id"},
    "figure": {"style"},
    "img": {"src", "width", "height", "sizes", "alt", "loading", "decoding"},
}
UNSAFE_LINK = re.compile(r"\s*(javascript|data|vbscript|file):", re.IGNORECASE)
WORD = re.compile(r"\w+")


def check_page(doc: convert.Doc, words: set[str] | None = None, lost_ok: frozenset = frozenset()) -> None:
    soup = BeautifulSoup(doc.html, "html.parser")
    ids = [tag["id"] for tag in soup.find_all(id=True)]
    assert len(ids) == len(set(ids)), f"an id twice: {sorted(i for i in ids if ids.count(i) > 1)}"
    for tag in soup.find_all(True):
        assert tag.name in ALLOWED, f"a tag convert doesn't keep: <{tag.name}>"
        assert set(tag.attrs) <= ALLOWED[tag.name], f"<{tag.name}> with {sorted(set(tag.attrs) - ALLOWED[tag.name])}"
        if tag.name == "div":
            assert tag["class"] == ["table-wrap"]
        if tag.name == "figure" and tag.get("style"):
            assert re.fullmatch(r"--width: \d{1,2}%", tag["style"])
        if tag.name == "a" and tag.get("href"):
            href = tag["href"]
            assert convert.SAFE_LINK.match(href.strip()) and not UNSAFE_LINK.match(href), f"an unsafe link: {href}"
            if href.startswith("#"):
                assert href == "#" or href[1:] in ids, f"a link to no place on the page: {href}"
    assert set(doc.links) == {a["href"] for a in soup.find_all("a", href=True)}

    again = convert.convert(
        doc.html, lambda src: {"src": src, "width": 8, "height": 8}, image_attrs, doc.title, doc.subtitle
    )
    assert _without_shares(again.html) == _without_shares(doc.html), "converting the page again changes it"

    if words is not None:
        shown = " ".join(
            [doc.title or "", doc.subtitle or "", soup.get_text(" "), *(i.get("alt", "") for i in soup("img"))]
        )
        shown = shown.replace("\xad", "")  # a soft hyphen: where a word may break
        seen, run_together = set(WORD.findall(shown.casefold())), soup.get_text("").replace("\xad", "").casefold()
        lost = {w for w in words if w not in seen and w not in run_together} - lost_ok
        assert not lost, f"words lost: {sorted(lost)}"


def _without_shares(html: str) -> str:
    """A page's HTML without the share of the text's width an image was given: a figure carries it,
    not the image convert reads it from."""
    return re.sub(r' style="--width: \d+%"| sizes="[^"]*"', "", html)


# Every word a reader sees in a document, read from the file itself.


def source_words(case: Case, data: bytes) -> set[str]:
    kind = case.folder.parent.name
    if kind == "word":
        return _word_words(data)
    if kind == "markdown":
        return _markdown_words(data, case.about.get("encoding"))
    return _gdoc_words(data)


# The elements of a Word paragraph that are text, and what those that aren't text in it stand for.
PIECES = {f"{W}tab": " ", f"{W}br": " ", f"{W}noBreakHyphen": "-", f"{W}softHyphen": ""}
READ = {f"{W}t", *PIECES}


def _word_words(data: bytes) -> set[str]:
    """The text of a Word file's body and notes; not its comments, page headers and footers, nor
    what tracked changes deleted (w:delText) or moved away (w:moveFrom)."""
    words = set()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        for part in ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml"):
            if part not in names:
                continue
            root = untrusted_xml(z.read(part))
            for moved in root.iter(f"{W}moveFrom"):
                moved.clear()
            for para in root.iter(f"{W}p"):
                text = "".join(PIECES.get(t.tag, t.text or "") for t in para.iter() if t.tag in READ)
                words |= set(WORD.findall(text.casefold()))
    return words


def _markdown_words(data: bytes, encoding: str | None) -> set[str]:
    """The text a Markdown file shows, front matter aside; HTML in it counts, shown as text, but
    for its comments."""
    text = data.decode(encoding or "utf-8-sig", errors="replace").replace("\r\n", "\n")
    _, text = markdown.front_matter(text)
    words = set()
    for token in markdown.MARKDOWN.parse(text):
        for t in [token, *(token.children or [])]:
            if t.type in ("text", "code_inline", "code_block", "fence"):
                words |= set(WORD.findall(t.content.casefold()))
            if t.type in ("html_block", "html_inline"):  # shown as text, but for its comments
                words |= set(WORD.findall(markdown.COMMENT.sub("", t.content).casefold()))
            if t.type == "image":
                words |= set(WORD.findall(t.content.casefold()))
    return words


def _gdoc_words(data: bytes) -> set[str]:
    """The text of a Docs export, its reviewers' comments aside."""
    html = unzip(data).html if data[:2] == b"PK" else data.decode()
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["head", "style", "script"]):
        tag.decompose()
    for a in soup.find_all("a", href=re.compile(r"^#cmnt")):
        if not a.decomposed:
            (a.find_parent("div") or a.find_parent(["sup", "span"]) or a).decompose()
    return set(WORD.findall((soup.body or soup).get_text(" ").casefold()))


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_every_case_holds(case):
    data = case.input.read_bytes()
    try:
        doc = page(case)
    except ValueError:
        assert "error" in expected(case)
        return
    check_page(doc, source_words(case, data), frozenset(case.about.get("lost", [])))


# Variants: a corpus file with its tags, attributes and lines dropped, repeated, swapped and cut short.


def mutate(case: Case, rng: random.Random) -> bytes:
    data = case.input.read_bytes()
    if case.input.suffix == ".md":
        return _mutate_lines(data, rng)
    if case.input.suffix == ".docx":
        if data[:2] != b"PK":
            return data
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            files = {name: z.read(name) for name in z.namelist()}
        files["word/document.xml"] = _mutate_xml(files["word/document.xml"], rng)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for name, content in files.items():
                z.writestr(name, content)
        return buf.getvalue()
    export = unzip(data) if data[:2] == b"PK" else DocExport(data.decode())
    soup = BeautifulSoup(export.html, "html.parser")
    _mutate_tree(soup.body or soup, rng, lambda el: list(el.attrs), lambda el: el.find_all(True))
    if not export.files:
        return str(soup).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("export.html", str(soup))
        for name, content in export.files.items():
            z.writestr(name, content)
    return buf.getvalue()


def _mutate_tree(root, rng: random.Random, attributes, elements) -> None:
    """Drop, repeat, swap or cut short a few of a tree's elements, or drop their attributes."""
    for _ in range(rng.randint(1, 6)):
        found = elements(root)
        if not found:
            return
        el = rng.choice(found)
        op = rng.choice(["drop", "repeat", "swap", "cut", "attribute"])
        if isinstance(el, Tag):
            siblings = [s for s in el.parent.children if isinstance(s, Tag)] if el.parent else []
            if op == "drop":
                el.decompose()
            elif op == "repeat":
                el.insert_after(copy.copy(el))
            elif op == "swap" and len(siblings) > 1:
                other = rng.choice(siblings)
                if other is not el:
                    other.insert_after(el.extract())
            elif op == "cut":
                for child in list(el.contents)[len(el.contents) // 2 :]:
                    child.extract()
            elif attrs := attributes(el):
                del el[rng.choice(attrs)]
        else:
            parent = _parents(root).get(el)
            if parent is None:
                continue
            children = list(parent)
            if op == "drop":
                parent.remove(el)
            elif op == "repeat":
                parent.insert(children.index(el) + 1, copy.deepcopy(el))
            elif op == "swap" and len(children) > 1:
                i, j = children.index(el), rng.randrange(len(children))
                children[i], children[j] = children[j], children[i]
                parent[:] = children
            elif op == "cut":
                del el[len(el) // 2 :]
            elif attrs := attributes(el):
                del el.attrib[rng.choice(attrs)]


def _parents(root) -> dict:
    return {child: parent for parent in root.iter() for child in parent}


def _mutate_xml(data: bytes, rng: random.Random) -> bytes:
    for _, (prefix, uri) in ElementTree.iterparse(io.BytesIO(data), events=["start-ns"]):
        ElementTree.register_namespace(prefix, uri)
    root = ElementTree.fromstring(data)
    body = root.find(f"{W}body")
    _mutate_tree(body, rng, lambda el: list(el.attrib), lambda el: list(el.iter())[1:])
    return ElementTree.tostring(root, xml_declaration=True, encoding="UTF-8")


def _mutate_lines(data: bytes, rng: random.Random) -> bytes:
    lines = data.split(b"\n")
    for _ in range(rng.randint(1, 6)):
        i = rng.randrange(len(lines))
        op = rng.choice(["drop", "repeat", "swap", "cut", "markup"])
        if op == "drop" and len(lines) > 1:
            del lines[i]
        elif op == "repeat":
            lines.insert(i, lines[i])
        elif op == "swap":
            j = rng.randrange(len(lines))
            lines[i], lines[j] = lines[j], lines[i]
        elif op == "cut":
            lines[i] = lines[i][: rng.randrange(len(lines[i]) + 1)]
        else:
            at = rng.randrange(len(lines[i]) + 1)
            lines[i] = (
                lines[i][:at]
                + rng.choice([b"#", b"*", b"_", b"`", b"[", b"](", b">", b"- ", b"|", b"<"])
                + lines[i][at:]
            )
    return b"\n".join(lines)


VARIANTS = 4


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_variants_of_every_case_hold(case):
    for seed in range(VARIANTS):
        rng = random.Random(f"{case.name}/{seed}")
        data = mutate(case, rng)
        try:
            doc = page(case, data)
        except ValueError:
            continue  # a file that can't be read says why, and nothing else
        try:
            check_page(doc)
        except AssertionError as e:
            raise AssertionError(f"variant {seed}: {e}") from None


# One sync and build of the whole corpus.


def test_the_whole_corpus_syncs_and_builds_in_every_theme(tmp_path):
    source = Corpus(CASES, prefix=lambda case: (case.folder.parent.name, case.folder.name))
    readable = {
        main_entry(source, c).id for c in CASES if "error" not in json.loads((c.folder / "expected.json").read_text())
    }
    settings = config.Settings("f", {"GOOGLE_API_KEY": "key"}, tmp_path, "https://example.org", "Ann", False)
    sync._failed.clear()
    assert sync.sync(settings, source) is True
    assert cache.Cache(settings.cache_dir).published() >= readable
    for theme in THEMES:
        built = dataclasses.replace(settings, theme_dir=Path("themes") / theme, output=theme)
        render.build(built)
        found = site.collect(built, themes.load(built.theme_dir))
        for item in found.items + found.pages:
            page_file = built.public_dir / item.url.strip("/") / "index.html"
            assert page_file.exists(), f"{theme}: no page for {item.url}"
