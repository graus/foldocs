"""Record Google Docs exports for the test corpus (tests/corpus/gdoc/): every Doc in a Drive folder
of sample Docs, exported the way the sync exports it (Drive.export_doc).

    GOOGLE_API_KEY=... uv run python scripts/record_corpus.py FOLDER_URL [--replace "A Name=Jan Jansen"]...

Each Doc becomes tests/corpus/gdoc/<its name, as a slug>/ with export.zip (its HTML and images, as
Drive zips them, the images made small), or export.html when it bundles no images: a Doc without
any, or one over Google's 10 MB export limit, whose images are links instead. Then write and review
their pages:

    UPDATE_CORPUS=1 uv run pytest tests/test_corpus.py

An export can name people: whoever commented, people in smart chips, with their addresses. Every
e-mail address becomes jan@example.com, each --replace is made, and links to Google's image servers
become placeholders; still, read the HTML before committing it. Better yet, keep the sample Docs in
an account of the persona's.
"""

import argparse
import html
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path

from PIL import Image

from foldocs.sources import DocExport
from foldocs.sources.drive import Drive, folder_id
from foldocs.text import slugify

CORPUS = Path(__file__).resolve().parent.parent / "tests" / "corpus" / "gdoc"
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
GOOGLE_IMAGE = re.compile(r"https://[\w.-]*googleusercontent\.com/[^\"'\s)]+")
IMAGE_MAX = 400  # px: a bundled image is made at most this wide or high
DOC = "application/vnd.google-apps.document"


def scrub(text: str, replace: dict[str, str]) -> str:
    """An export without the names and addresses it carries."""
    for old, new in replace.items():
        text = text.replace(old, new).replace(html.escape(old), html.escape(new))
    text = EMAIL.sub("jan@example.com", text)
    images: dict[str, str] = {}
    return GOOGLE_IMAGE.sub(
        lambda m: images.setdefault(m[0], f"https://lh3.googleusercontent.com/corpus-{len(images) + 1}"), text
    )


def shrink(data: bytes) -> bytes:
    """An image at most IMAGE_MAX wide or high, in its own format; anything else as it is."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:
        return data
    if max(image.size) <= IMAGE_MAX:
        return data
    image.thumbnail((IMAGE_MAX, IMAGE_MAX))
    out = io.BytesIO()
    image.save(out, image.format or "PNG")
    return out.getvalue()


def save(export: DocExport, folder: Path, replace: dict[str, str]) -> Path:
    """An export, scrubbed, in a case's folder: export.zip, or export.html for one without images
    bundled (a Doc with none, or the fallback for one too large to zip)."""
    folder.mkdir(parents=True, exist_ok=True)
    page = scrub(export.html, replace)
    for stale in ("export.zip", "export.html"):
        (folder / stale).unlink(missing_ok=True)
    if not export.files:
        (path := folder / "export.html").write_text(page)
        return path
    with zipfile.ZipFile(path := folder / "export.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("export.html", page)  # Drive names it after the Doc
        for name, data in sorted(export.files.items()):
            z.writestr(name, shrink(data))
    return path


def record(source, out: Path, replace: dict[str, str]) -> list[str]:
    """Every Doc in the source's folder, as a case in `out`; returns the cases' names."""
    entries, _ = source.walk()
    cases = []
    for entry in sorted((e for e in entries if e.mime == DOC), key=lambda e: e.name):
        case = slugify(scrub(entry.name, replace), "doc")
        folder = out / case
        save(source.export_doc(entry), folder, replace)
        if not (about := folder / "case.toml").exists():
            about.write_text(f"about = {json.dumps(scrub(entry.name, replace), ensure_ascii=False)}\n")  # TOML too
        cases.append(case)
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", help="the Drive folder's share link")
    parser.add_argument("--replace", action="append", default=[], metavar="OLD=NEW", help="text to replace")
    parser.add_argument("--out", type=Path, default=CORPUS)
    args = parser.parse_args()
    if not (key := os.environ.get("GOOGLE_API_KEY")):
        sys.exit("GOOGLE_API_KEY is not set")
    replace = dict(pair.split("=", 1) for pair in args.replace)
    for case in record(Drive(key, folder_id(args.folder)), args.out, replace):
        print(f"gdoc/{case}")


if __name__ == "__main__":
    main()
