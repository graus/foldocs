"""Fonts that make text code (a file name, a setting), as Docs exports and Word files say so."""

# A Word file says which of its fonts are monospace (fixed pitch, in its font table); a Docs export
# names only the family. These are the families Google Fonts files as Monospace (from
# https://fonts.google.com/metadata/fonts), with the monospace fonts Docs and Word offer that aren't
# Google Fonts.
MONOSPACE = frozenset(
    name.lower()
    for name in (
        "Anonymous Pro", "Azeret Mono", "B612 Mono", "Chivo Mono", "Courier Prime", "Cousine", "Cutive Mono",
        "DM Mono", "Datatype", "Fira Code", "Fira Mono", "Fragment Mono", "Geist Mono", "Google Sans Code",
        "Hibur Mono", "IBM Plex Mono", "Inconsolata", "Intel One Mono", "Iosevka Charon", "Iosevka Charon Mono",
        "JetBrains Mono", "Kode Mono", "LXGW WenKai Mono TC", "Lekton", "Libertinus Mono", "Lilex",
        "M PLUS 1 Code", "Major Mono Display", "Martian Mono", "Monofett", "Nova Mono", "Overpass Mono",
        "Oxygen Mono", "PT Mono", "Red Hat Mono", "Reddit Mono", "Roboto Mono", "Share Tech Mono", "Sixtyfour",
        "Sixtyfour Convergence", "Sometype Mono", "Source Code Pro", "Space Mono", "Spline Sans Mono",
        "Syne Mono", "Ubuntu Mono", "Ubuntu Sans Mono", "VT323", "Victor Mono", "Workbench", "Xanh Mono",
        # not Google Fonts
        "Courier New", "Courier", "Consolas", "Lucida Console", "Lucida Sans Typewriter", "Menlo", "Monaco",
        "SF Mono", "Cascadia Code", "Cascadia Mono", "Andale Mono", "Liberation Mono", "DejaVu Sans Mono",
        "monospace",
    )
)  # fmt: skip


def monospace(font_family: str, also: frozenset[str] = frozenset()) -> bool:
    """Whether a CSS font-family (or a Word font name) is a monospace font."""
    names = (name.strip().strip("\"'").lower() for name in font_family.split(","))
    return any(name in MONOSPACE or name in also for name in names)
