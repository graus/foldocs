"""The site's own design: a few design tokens set in a Doc, on top of the theme's.

A Doc named "design" in the root folder holds a table, a row per setting:

  Setting        Light      Dark
  Accent         #2156c4    #8fb0ff
  Background     #fffdf7
  Heading font   Fraunces, serif
  Corners        6px

The first row is a header, unless it names a setting. A setting left out, or a cell left empty, keeps the
theme's value, so the Dark column is optional; fonts and corners have one value for both modes.
The same settings apply to every theme and variant: all themes share these tokens.

Every value is checked against what its setting accepts, and only a value that passes reaches
the stylesheet; anything else is logged and left out. A font Google Fonts has is downloaded with
the Doc and served from the site itself (no requests to Google from the visitor's browser); any
other name is kept, for visitors who have it installed.

parse() and fonts() run in the sync, with the Doc; stylesheet() in the build.
"""

import hashlib
import logging
import re
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote_plus, unquote_plus

import requests
from bs4 import BeautifulSoup

from .text import plain

log = logging.getLogger(__name__)

# The settings, by the name in the Doc (lower case), with the token each one sets.
COLORS = {"accent": "color-accent", "background": "color-bg", "text": "color-text"}
FONTS = {"heading font": "font-heading", "body font": "font-body", "ui font": "font-ui"}
LENGTHS = {"corners": "radius"}
ALIASES = {
    "interface font": "ui font",
    "label font": "ui font",
    "text font": "body font",
    "radius": "corners",
    "corner radius": "corners",
}

# With "#": 3, 4, 6 or 8 hex digits. Without: only 6 or 8, so a word like "bad" or "fed" isn't a color.
COLOR = re.compile(r"#([0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})|([0-9a-f]{6}|[0-9a-f]{8})", re.IGNORECASE)
LENGTH = re.compile(r"0|\d{1,3}(\.\d{1,3})?(px|rem|em)")
FAMILY = re.compile(r"[A-Za-z0-9][A-Za-z0-9 \-]{0,59}")
GENERIC = {"serif", "sans-serif", "monospace", "cursive", "system-ui", "ui-serif", "ui-sans-serif", "ui-monospace"}
# A font's page on Google Fonts, pasted in place of its name.
SPECIMEN = re.compile(r"(?:https?://)?fonts\.google\.com/specimen/([^/?#\s]+)")
FALLBACK = "system-ui, sans-serif"  # after a stack that names no generic family
FONT_TIMEOUT = 30  # seconds, for one request to Google Fonts

Design = dict[str, list]  # setting -> [light, dark]; a font's value is its families


def _setting(name: str) -> str | None:
    name = re.sub(r"[\s_-]+", " ", name.strip().lower().rstrip(":"))
    name = re.sub(r" colou?r$", "", name)  # "Accent color" (or "colour") is "Accent"
    name = ALIASES.get(name, name)
    return name if name in COLORS | FONTS | LENGTHS else None


def _color(value: str) -> str | None:
    m = COLOR.fullmatch(value)
    return f"#{(m[1] or m[2]).lower()}" if m else None


def _length(value: str) -> str | None:
    return value.lower() if LENGTH.fullmatch(value.lower()) else None


def _families(value: str) -> list[str] | None:
    """A font stack: family names and generic families, comma-separated; None if any is invalid."""
    value = SPECIMEN.sub(lambda m: unquote_plus(m[1]), value)  # the link to a font's page: its name
    families = [f.strip().strip("\"'").strip() for f in value.split(",")]
    for f in families:
        if f.lower() not in GENERIC and not FAMILY.fullmatch(f):
            return None
    return [f.lower() if f.lower() in GENERIC else f for f in families] or None


def parse(html: str, name: str = "design") -> Design:
    """The settings in the Doc's tables (as convert.convert renders them), each value checked."""
    design: Design = {}
    for table in BeautifulSoup(html, "html.parser").find_all("table"):
        for n, row in enumerate(table.find_all("tr")):
            cells = [plain(td).strip() for td in row.find_all(["td", "th"])]
            if not cells or not cells[0]:
                continue
            setting = _setting(cells[0])
            if setting is None:
                if n:  # the first row is the header, unless it names a setting
                    log.warning("%s: no setting %r; it is left out", name, cells[0])
                continue
            light, dark = (cells[1:3] + ["", ""])[:2]
            if setting in FONTS or setting in LENGTHS:
                if dark:
                    log.warning("%s: %s has one value for both modes; %r is left out", name, cells[0], dark)
                check, dark = (_families if setting in FONTS else _length), ""
            else:
                check = _color
            values = []
            for value in (light, dark):
                valid = check(value) if value else None
                if value and valid is None:
                    log.warning("%s: %r is not a valid value for %s; it is left out", name, value, cells[0])
                values.append(valid)
            if any(values):
                design[setting] = values
    return design


# Google Fonts ------------------------------------------------------------------------------------

CSS_API = "https://fonts.googleapis.com/css2"
# Google serves woff2 only to a browser it knows to support it.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0"
# The styles asked for, most first: a family that lacks one is refused as a whole, so then fewer.
STYLES = ("ital,wght@0,400;0,600;0,700;1,400;1,700", "wght@400;600;700", "wght@400;700", "")
FACE = re.compile(r"@font-face\s*\{([^}]*)\}")
FONT_FILE = re.compile(r"url\((https://fonts\.gstatic\.com/[\w./-]+\.woff2)\)")
WEIGHT = re.compile(r"\d{3}( \d{3})?")
UNICODE_RANGE = re.compile(r"[Uu+0-9A-Fa-f?, -]+")


class NotOnGoogle(Exception):
    pass


def fetch(url: str) -> bytes:
    """GET a Google Fonts URL; NotOnGoogle for a family it doesn't have (any other error raises)."""
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=FONT_TIMEOUT)
    if resp.status_code == 400:
        raise NotOnGoogle(url)
    resp.raise_for_status()
    return resp.content


def fonts(design: Design, out_dir: Path, base: str, get: Callable[[str], bytes] = fetch) -> str:
    """@font-face rules for every family in the design that Google Fonts has, its files downloaded
    into out_dir (served at `base`). Rebuilt from the parts that pass a check, never copied."""
    families = {f for setting in FONTS if setting in design for f in design[setting][0] if f not in GENERIC}
    faces = []
    for family in sorted(families):
        for styles in STYLES:
            spec = quote_plus(family) + (f":{styles}" if styles else "")
            try:
                css = get(f"{CSS_API}?family={spec}&display=swap").decode()
                break
            except NotOnGoogle:
                continue
        else:
            log.info("Font %r is not on Google Fonts: used only where it is installed", family)
            continue
        for block in FACE.findall(css):
            props = dict((k.strip(), v.strip()) for k, v in re.findall(r"([\w-]+)\s*:\s*([^;]+);", block))
            src = FONT_FILE.search(props.get("src", ""))
            style, weight, ranges = props.get("font-style"), props.get("font-weight", ""), props.get("unicode-range")
            if not src or style not in ("normal", "italic") or not WEIGHT.fullmatch(weight):
                continue
            name = f"font-{hashlib.sha1(src[1].encode()).hexdigest()[:12]}.woff2"
            if not (out_dir / name).exists():  # a variable font serves several weights from one file
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / name).write_bytes(get(src[1]))
            face = [f'font-family: "{family}"', f"font-style: {style}", f"font-weight: {weight}"]
            face += ["font-display: swap", f'src: url({base}{name}) format("woff2")']
            if ranges and UNICODE_RANGE.fullmatch(ranges):
                face.append(f"unicode-range: {ranges}")
            faces.append("@font-face { " + "; ".join(face) + "; }")
    return "\n".join(faces)


# The stylesheet ----------------------------------------------------------------------------------

# Colors the theme derives from the ones set, so a new background or text color carries through
# to panels, bands, borders and secondary text instead of clashing with the theme's own.
NEUTRALS = {
    "color-surface": "var(--color-bg)",
    "color-band": "color-mix(in oklab, var(--color-bg), var(--color-text) 6%)",
    "color-text-secondary": "color-mix(in oklab, var(--color-text) 82%, var(--color-bg))",
    "color-text-muted": "color-mix(in oklab, var(--color-text) 65%, var(--color-bg))",
    "color-border": "color-mix(in oklab, var(--color-text) 16%, var(--color-bg))",
}
# The dark band: the text color in light mode; in dark mode, a step up from the background.
INVERSE_LIGHT = {
    "color-inverse-bg": "var(--color-text)",
    "color-inverse-text": "var(--color-bg)",
    "color-inverse-muted": "color-mix(in oklab, var(--color-bg) 75%, var(--color-text))",
}
INVERSE_DARK = {
    "color-inverse-bg": "color-mix(in oklab, var(--color-bg), var(--color-text) 10%)",
    "color-inverse-text": "var(--color-text)",
    "color-inverse-muted": "color-mix(in oklab, var(--color-text) 75%, var(--color-bg))",
}
ACCENTS = {
    "color-accent-text": "var(--color-accent)",
    "color-focus": "var(--color-accent)",
    "color-inverse-accent": "color-mix(in oklab, var(--color-accent), var(--color-inverse-text) 35%)",
}


def _font(families: list[str]) -> str:
    stack = ", ".join(f if f in GENERIC else f'"{f}"' for f in families)
    return stack if any(f in GENERIC for f in families) else f"{stack}, {FALLBACK}"


def _rule(selector: str, tokens: dict[str, str], indent: str = "") -> str:
    lines = "".join(f"{indent}  --{name}: {value};\n" for name, value in tokens.items())
    return f"{indent}{selector} {{\n{lines}{indent}}}\n" if tokens else ""


def stylesheet(design: Design, faces: str = "") -> str:
    """The CSS for the design, loaded after the theme's stylesheets; empty for no design."""
    if not design:
        return ""
    both: dict[str, str] = {}  # both modes
    light: dict[str, str] = {}
    dark: dict[str, str] = {}
    for setting, token in COLORS.items():
        if setting in design:
            for mode, value in zip((light, dark), design[setting], strict=True):
                if value:
                    mode[token] = value
    if "background" in design or "text" in design:
        both |= NEUTRALS
        light |= INVERSE_LIGHT
        dark |= INVERSE_DARK
    if "accent" in design:
        both |= ACCENTS
    for setting, token in FONTS.items():
        if setting in design:
            both[token] = _font(design[setting][0])
    if "corners" in design:
        both["radius"] = design["corners"][0]
    return (
        "/* The site's own design, from the design Doc in its folder (foldocs/design.py) */\n"
        + (faces + "\n" if faces else "")
        + _rule(":root", both)
        + (f"@media (prefers-color-scheme: light) {{\n{_rule(':root', light, '  ')}}}\n" if light else "")
        + (f"@media (prefers-color-scheme: dark) {{\n{_rule(':root', dark, '  ')}}}\n" if dark else "")
    )
