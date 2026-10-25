"""A Markdown file (.md), as CommonMark with tables and ~~strikethrough~~. HTML written in the
file is shown as the text it is, not read as markup. Its images are other files in the folder,
linked by a path from its own (as Nextcloud's Text app keeps them, in a hidden .attachments
folder beside it), or inside it as data: URIs."""

import re

from markdown_it import MarkdownIt

from . import DocText, Reading, inline_image

MARKDOWN = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
# Between --- lines at the top, as static site generators write it.
FRONT_MATTER = re.compile(r"\A---[ \t]*\n(.*?)^---[ \t]*$\n?", re.DOTALL | re.MULTILINE)
# What front matter is made of: `key: value` lines, the indented lines of a longer value, comments.
FRONT_LINE = re.compile(r"[\w-]+[ \t]*:.*|[ \t]+.*|#.*|[ \t]*")


def read(file: Reading) -> DocText:
    """A heading that opens the file is its title, as in a Doc; front matter can name a title and
    a subtitle (or description) instead, and is otherwise left out."""
    text = file.download().decode("utf-8-sig", errors="replace").replace("\r\n", "\n")  # as saved on Windows
    meta, text = front_matter(text)

    def image(src: str) -> bytes:
        if src.startswith("data:"):
            return inline_image(src)
        linked = file.linked(src)
        if linked is None:
            raise ValueError(f"Not a file in the folder: {src[:80]}")
        if linked.kind != "image":
            raise ValueError(f"Not an image: {linked.name}")
        return file.download(linked)

    html = f"<html><body>{MARKDOWN.render(text)}</body></html>"
    return DocText(html, image, meta.get("title"), meta.get("subtitle") or meta.get("description"))


def front_matter(text: str) -> tuple[dict[str, str], str]:
    """The `key: value` lines of a Markdown file's front matter (with no value of more lines, as
    YAML can have: Foldocs reads only a title and a subtitle), and the text after it. A file that
    opens with a rule (---) and has text that isn't front matter before the next one keeps it all."""
    m = FRONT_MATTER.match(text)
    lines = m[1].splitlines() if m else []
    if not m or not all(FRONT_LINE.fullmatch(line) for line in lines) or not any(":" in line for line in lines):
        return {}, text
    meta = {}
    for line in lines:
        key, sep, value = line.partition(":")
        if sep and key.strip() and not line[0].isspace() and value.strip():
            meta[key.strip().lower()] = value.strip().strip("\"'")
    return meta, text[m.end() :]
