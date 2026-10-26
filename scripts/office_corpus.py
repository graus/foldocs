"""Write the test corpus's real Word files: one sample document (make_corpus.word_sample), opened and
saved by each program that writes Word files, so each case is that program's own XML.

    uv run python scripts/office_corpus.py            # LibreOffice: from-libreoffice, old-doc
    uv run python scripts/office_corpus.py sample.docx  # the sample, for another program

LibreOffice runs headless. Word, Pages, OnlyOffice and Google Docs keep files in sandboxes a script
can't reach, so for those, open the sample in the program, save it as a Word file (.docx), and put
it in tests/corpus/word/from-<program>/input.docx.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

from make_corpus import CORPUS, word_sample

SOFFICE = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")


def libreoffice(data: bytes, to: str) -> bytes:
    """The document as LibreOffice saves it: "docx", or "doc" (Word 97-2003)."""
    filters = {"docx": "docx:MS Word 2007 XML", "doc": "doc:MS Word 97"}
    with tempfile.TemporaryDirectory() as tmp:
        source, out = Path(tmp) / "sample.docx", Path(tmp) / "out"
        source.write_bytes(data)
        profile = f"-env:UserInstallation=file://{tmp}/profile"  # not the user's own LibreOffice settings
        subprocess.run(
            [str(SOFFICE), profile, "--headless", "--convert-to", filters[to], "--outdir", str(out), str(source)],
            check=True,
            capture_output=True,
            timeout=120,
        )
        return (out / f"sample.{to}").read_bytes()


def main() -> None:
    if sys.argv[1:]:
        Path(sys.argv[1]).write_bytes(word_sample())
        return
    if not SOFFICE.exists():
        raise SystemExit(f"LibreOffice is not installed at {SOFFICE}")
    for case, to in (("from-libreoffice", "docx"), ("old-doc", "doc")):  # old-doc: a .doc renamed .docx
        folder = CORPUS / "word" / case
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "input.docx").write_bytes(libreoffice(word_sample(), to))
        print(f"word/{case}")


if __name__ == "__main__":
    main()
