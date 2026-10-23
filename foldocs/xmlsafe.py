"""XML from outside: a folder's Word files and a server's answers, read only without a DTD.

Neither ever needs one, and the entities a DTD declares are how XML parsers are attacked (expat's
CVE-2024-8176, among others), so XML with one is refused before anything in it is expanded,
whichever expat this Python has."""

import io
import xml.parsers.expat
import zipfile
from xml.etree import ElementTree

WORD_PART_MAX = 64 << 20  # bytes: one XML part of a Word file, unpacked
WORD_MAX = 100 << 20  # bytes: a whole Word file, unpacked (its images included)


def _refuse_dtd(data: bytes, strict: bool = True) -> None:
    """Raises on a DTD, and on anything but XML. Not `strict` (a Word file's other parts, such as
    its images): data that isn't XML passes, since a DTD is only ever at the start, and so does a
    doctype that declares nothing, like an SVG's naming its standard DTD: nothing reads that DTD,
    so only declarations inside the file could be expanded."""

    def refuse(*_):
        raise ElementTree.ParseError("a DTD in untrusted XML")

    def doctype(name, system_id, public_id, has_internal_subset):
        if strict or has_internal_subset:
            refuse()

    check = xml.parsers.expat.ParserCreate()
    check.StartDoctypeDeclHandler = doctype
    check.EntityDeclHandler = refuse
    try:
        check.Parse(data, True)
    except xml.parsers.expat.ExpatError as e:
        if strict:
            raise ElementTree.ParseError(str(e)) from None


def untrusted_xml(data: bytes) -> ElementTree.Element:
    """XML from a folder or a server, parsed only if it has no DTD."""
    _refuse_dtd(data)
    return ElementTree.fromstring(data)


def check_word_file(data: bytes) -> None:
    """Refuses a Word file that unpacks too large, or has a DTD in any of its parts: the converter
    reads them with a parser that would expand it. Every part, whatever its name, since the file
    itself says which part is the document."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        parts = z.infolist()
        if sum(part.file_size for part in parts) > WORD_MAX:
            raise ValueError("a Word file that unpacks too large")
        for part in parts:
            named_xml = part.filename.endswith((".xml", ".rels"))
            if named_xml and part.file_size > WORD_PART_MAX:
                raise ValueError(f"a Word file with a part too large: {part.filename}")
            _refuse_dtd(z.read(part), strict=named_xml)
