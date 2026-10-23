"""Check a theme against the shared design system: its tokens, and WCAG AA contrast.

Every theme defines the same design tokens (CSS custom properties) with its own values, so a
theme can be checked without knowing its design: the colors are read from the stylesheets it
loads (its parents' first), in light and in dark mode, and every pair of text on background the
themes use is held to WCAG 2.2 AA: 4.5:1 for text, 3:1 for large text and for shapes such as
focus rings and accent marks.

Run it with `foldocs check-theme [folder]`; the tests run it for every bundled theme.
"""

import re

from .theme import Theme

COLOR_TOKENS = (
    "bg", "surface", "band", "text", "text-secondary", "text-muted", "border",
    "accent", "accent-text", "inverse-bg", "inverse-text", "inverse-muted", "inverse-accent", "focus",
)  # fmt: skip
OTHER_TOKENS = (
    "font-body", "font-heading", "font-ui", "text-sm", "text-base", "text-lg", "text-xl", "text-2xl",
    "space-1", "space-2", "space-3", "space-4", "space-5", "width-page", "width-text", "radius",
)  # fmt: skip

HEX = re.compile(r"#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?")
TEXT = 4.5  # normal text
LARGE = 3.0  # large text (24px, or 18.7px bold) and graphical objects
PAIRS = [
    *(
        (fg, bg, TEXT)
        for fg in ("text", "text-secondary", "text-muted", "accent-text")
        for bg in ("bg", "surface", "band")
    ),
    ("inverse-text", "inverse-bg", TEXT),
    ("inverse-muted", "inverse-bg", TEXT),
    ("inverse-accent", "inverse-bg", TEXT),
    ("accent", "bg", LARGE),
    ("focus", "bg", LARGE),
    ("focus", "surface", LARGE),
]


def tokens(theme: Theme) -> tuple[dict[str, str], dict[str, str]]:
    """The theme's custom properties, light and dark, as its stylesheets set them in order."""
    files = theme.static_files()
    return tokens_in("\n".join(files[name].decode() for name in theme.stylesheets if name in files))


def tokens_in(css: str) -> tuple[dict[str, str], dict[str, str]]:
    """The custom properties stylesheets set, light and dark, in the order given."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    # As the cascade does: a later block wins. A plain :root applies in both modes, so it also
    # overrides an earlier stylesheet's dark values; a dark block applies in dark mode only.
    light, dark = {}, {}
    for block in re.finditer(r"(@media[^{]*\{\s*)?:root[^{]*\{([^}]*)\}", css):
        media = block[1] or ""
        values = re.findall(r"--([\w-]+)\s*:\s*([^;]+);", block[2])
        if not media:
            light.update(values)
            dark.update(values)
        elif "dark" in media:
            dark.update(values)
        elif "light" in media:  # the design document's light colors
            light.update(values)
        # other media queries only change sizes
    return light, dark


def resolve(values: dict[str, str], name: str) -> str:
    """A token's value, following var(--other) to the value it names."""
    value, seen = values[name].strip(), {name}
    while (m := re.fullmatch(r"var\(--([\w-]+)\)", value)) and m[1] in values and m[1] not in seen:
        seen.add(m[1])
        value = values[m[1]].strip()
    return value


def _luminance(color: str) -> float:
    if not HEX.fullmatch(color.strip()):
        raise ValueError(color)
    hex_ = color.strip().lstrip("#")
    if len(hex_) == 3:
        hex_ = "".join(c * 2 for c in hex_)
    channels = [int(hex_[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    r, g, b = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def check(theme: Theme) -> list[str]:
    """Every problem found, as a sentence; an empty list means the theme passes."""
    light, dark = tokens(theme)
    required = [*(f"color-{c}" for c in COLOR_TOKENS), *OTHER_TOKENS]
    problems = [f"missing token --{name}" for name in required if name not in light]
    if problems:
        return problems
    for mode, values in (("light", light), ("dark", dark)):
        colors = {name: resolve(values, name) for name in (f"color-{c}" for c in COLOR_TOKENS)}
        unknown = [name for name, value in colors.items() if not HEX.fullmatch(value)]
        problems += [f"{mode}: --{name} is {colors[name]}, not a hex color this check can read" for name in unknown]
        for fg, bg, needed in PAIRS:
            if f"color-{fg}" in unknown or f"color-{bg}" in unknown:
                continue
            ratio = contrast(colors[f"color-{fg}"], colors[f"color-{bg}"])
            if ratio < needed:
                problems.append(f"{mode}: --color-{fg} on --color-{bg} is {ratio:.2f}:1, needs {needed}:1")
    return problems
