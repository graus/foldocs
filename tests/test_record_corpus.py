"""The script that records Google Docs exports for the corpus, against a fake Drive: what it saves
reads as the sync reads an export, without the names and addresses the Doc carried."""

import importlib.util
import io
import tomllib
import zipfile
from pathlib import Path

from PIL import Image

from foldocs import convert
from foldocs.formats import gdoc
from foldocs.sources import DocExport, Entry
from foldocs.sources.drive import unzip

SCRIPT = Path(__file__).parent.parent / "scripts" / "record_corpus.py"
spec = importlib.util.spec_from_file_location("record_corpus", SCRIPT)
record_corpus = importlib.util.module_from_spec(spec)
spec.loader.exec_module(record_corpus)

DOC = "application/vnd.google-apps.document"


def png(size: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), "teal").save(buf, "PNG")
    return buf.getvalue()


SMALL_DOC = (
    '<html><head><style>.c1{font-weight:700}</style></head><body><p class="title"><span>Ann\'s trip</span></p>'
    '<p><span>Planned with </span><a href="mailto:ann.real@gmail.example">Ann Real</a><span>.</span></p>'
    '<p><span><img src="images/image1.png" style="width: 600px"></span></p>'
    '<div><p><a href="#cmnt_ref1" id="cmnt1">[a]</a><span>Ann Real: check this, ann.real@gmail.example</span></p></div>'
    "</body></html>"
)
LARGE_DOC = (
    '<html><body><p><span>Too large to zip.</span></p><p><img src="https://lh3.googleusercontent.com/AbC-secret_1">'
    '<img src="https://lh3.googleusercontent.com/AbC-secret_1"></p></body></html>'
)


class FakeDrive:
    def __init__(self):
        self.entries = [
            Entry("1", "Ann Real's notes", DOC, [], "", ""),
            Entry("2", "Big one", DOC, [], "", ""),
            Entry("3", "photo.jpg", "image/jpeg", [], "", ""),
        ]

    def walk(self):
        return self.entries, []

    def export_doc(self, entry):
        if entry.id == "1":
            return DocExport(SMALL_DOC, {"images/image1.png": png(1200)})
        return DocExport(LARGE_DOC)  # over the limit: plain HTML, images by URL


def test_every_doc_is_recorded_as_its_export_without_names_or_addresses(tmp_path):
    cases = record_corpus.record(FakeDrive(), tmp_path, {"Ann Real": "Jan Jansen"})
    assert sorted(cases) == ["big-one", "jan-jansen-s-notes"]  # the photo is no Doc

    export = unzip((tmp_path / "jan-jansen-s-notes" / "export.zip").read_bytes())
    assert "Ann Real" not in export.html and "ann.real" not in export.html
    assert 'href="mailto:jan@example.com">Jan Jansen</a>' in export.html
    assert Image.open(io.BytesIO(export.files["images/image1.png"])).size == (400, 400)
    assert tomllib.loads((tmp_path / "jan-jansen-s-notes" / "case.toml").read_text()) == {"about": "Jan Jansen's notes"}

    text = gdoc.clean(export, fetch=None)  # as the sync reads it
    doc = convert.convert(text.body, lambda src: {"src": src}, lambda info, sizes=None: info, text.title)
    assert doc.title == "Ann's trip" and "check this" not in doc.html and len(doc.images) == 1

    large = (tmp_path / "big-one" / "export.html").read_text()
    assert "secret" not in large and large.count("https://lh3.googleusercontent.com/corpus-1") == 2


def test_recording_again_replaces_the_export(tmp_path):
    record_corpus.record(FakeDrive(), tmp_path, {})
    (tmp_path / "big-one" / "case.toml").write_text('about = "Written by hand"\n')
    drive = FakeDrive()
    drive.export_doc = lambda entry: DocExport(SMALL_DOC, {"images/image1.png": png(10)})
    record_corpus.record(drive, tmp_path, {})
    assert sorted(p.name for p in (tmp_path / "big-one").iterdir()) == ["case.toml", "export.zip"]
    assert (tmp_path / "big-one" / "case.toml").read_text() == 'about = "Written by hand"\n'
    with zipfile.ZipFile(tmp_path / "big-one" / "export.zip") as z:
        assert z.namelist() == ["export.html", "images/image1.png"]
