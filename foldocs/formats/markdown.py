"""A Markdown file (.md), as CommonMark with tables and ~~strikethrough~~. HTML written in the
file is shown as the text it is, not read as markup; an HTML comment (Hugo's <!--more-->) is left
out. Its images are other files in the folder, linked by a path from its own (as Nextcloud's Text
app keeps them, in a hidden .attachments folder beside it), or inside it as data: URIs.

Its text is UTF-8, as nearly every editor saves it; or UTF-16 with a byte-order mark, as Windows
Notepad saves "Unicode"; or else Windows-1252, as older Windows editors save."""

import codecs
import json
import re
import tomllib

from bs4 import BeautifulSoup
from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml

from . import DocText, Reading, inline_image

MARKDOWN = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _html_as_text(self, tokens, idx, options, env) -> str:
    """HTML in the file, as the text it is; a comment, as nothing."""
    token = tokens[idx]
    html = COMMENT.sub("", token.content).strip("\n")
    if not html.strip():
        return ""
    return f"<p>{escapeHtml(html)}</p>\n" if token.type == "html_block" else escapeHtml(html)


MARKDOWN.add_render_rule("html_block", _html_as_text)
MARKDOWN.add_render_rule("html_inline", _html_as_text)

# Front matter, as static site generators write it: YAML between --- lines (or closed by ..., as
# Pandoc does), TOML between +++ lines (Hugo's own), or a JSON object (Hugo reads that too).
FRONT_MATTER = re.compile(r"\A---[ \t]*\n(.*?)^(?:---|\.\.\.)[ \t]*$\n?", re.DOTALL | re.MULTILINE)
TOML_FRONT_MATTER = re.compile(r"\A\+\+\+[ \t]*\n(.*?)^\+\+\+[ \t]*$\n?", re.DOTALL | re.MULTILINE)
# What front matter is made of: `key: value` lines, the items and indented lines of a longer value,
# comments.
KEY = re.compile(r"([\w-]+)[ \t]*:(.*)")
FRONT_LINE = re.compile(rf"{KEY.pattern}|-([ \t].*)?|[ \t]+.*|#.*|[ \t]*")


def read(file: Reading) -> DocText:
    """A heading that opens the file is its title, as in a Doc; front matter can name a title and
    a subtitle (or description) instead, and is otherwise left out."""
    meta, text = front_matter(decode(file.download()))

    def image(src: str) -> bytes:
        if src.startswith("data:"):
            return inline_image(src)
        linked = file.linked(src)
        if linked is None:
            raise ValueError(f"Not a file in the folder: {src[:80]}")
        if linked.kind != "image":
            raise ValueError(f"Not an image: {linked.name}")
        return file.download(linked)

    body = BeautifulSoup(MARKDOWN.render(text), "html.parser")
    return DocText(body, image, meta.get("title"), meta.get("subtitle") or meta.get("description"))


def decode(data: bytes) -> str:
    """A Markdown file's text, with Windows line endings as any other. Raises ValueError for a file
    that is no text (one with NUL characters in it, such as an image named .md)."""
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = data.decode("utf-16", errors="replace")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")
    if "\0" in text:
        raise ValueError("not a text file, though named a Markdown file")
    return text.replace("\r\n", "\n")


def front_matter(text: str) -> tuple[dict[str, str], str]:
    """A Markdown file's front matter, as its text values (Foldocs reads only a title and a
    subtitle), and the text after it. Of YAML, the `key: value` lines (with no value of more lines,
    as YAML can have); a file that opens with a rule (---) and has text that isn't front matter
    before the next one keeps it all. TOML or JSON that doesn't parse is text too."""
    if m := TOML_FRONT_MATTER.match(text):
        try:
            return _texts(tomllib.loads(m[1])), text[m.end() :]
        except tomllib.TOMLDecodeError:
            return {}, text
    if text.startswith("{"):
        try:
            data, end = json.JSONDecoder().raw_decode(text)
        except ValueError:
            return {}, text
        line_end = re.compile(r"[ \t]*(\n|\Z)").match(text, end)  # the object ends its line
        if not isinstance(data, dict) or not line_end:
            return {}, text
        return _texts(data), text[line_end.end() :]
    m = FRONT_MATTER.match(text)
    lines = m[1].splitlines() if m else []
    keys = [found for line in lines if (found := KEY.fullmatch(line))]
    if not m or not keys or not all(FRONT_LINE.fullmatch(line) for line in lines):
        return {}, text
    meta = {key[1].lower(): value for key in keys if (value := _value(key[2]))}
    return meta, text[m.end() :]


def _texts(data: dict) -> dict[str, str]:
    return {key.lower(): value.strip() for key, value in data.items() if isinstance(value, str) and value.strip()}


def _value(raw: str) -> str | None:
    """A front matter value as text: without the quotes around it, or a comment after it; None
    for none, or for one on the lines that follow (> or |)."""
    value = raw.strip()
    if re.fullmatch(r"[>|][+-]?\d*", value):
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return re.sub(r"\s+#.*", "", value) or None
