"""A theme: the templates, static files and words that give the site its look and language.

The engine (sync, conversion, build) has neither. A theme is a folder with:
  theme.toml   its words and addresses (see themes/default/theme.toml)
  templates/   Jinja templates: base, home, item, list (and 404, feed.xml, sitemap.xml, from base)
  static/      CSS, fonts and other files, published under /static/

A theme can build on another with `extends = "<theme>"` in its theme.toml: a bundled theme's
name (themes/<name>) or a path relative to the theme. It then needs only what differs.
Templates and static files are looked up in the theme first, then in what it extends; its
theme.toml is merged over its parent's, table by table; and its `stylesheets` are loaded after
its parent's, so a few lines of CSS can override the parent's design tokens.
"""

import hashlib
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# The themes that come with the engine, so a theme anywhere can extend one by name: inside the
# package when installed, beside it in a checkout of the repository.
_PACKAGE = Path(__file__).resolve().parent
BUNDLED = next((p for p in (_PACKAGE / "themes", _PACKAGE.parent / "themes") if p.is_dir()), _PACKAGE / "themes")
DEFAULT = BUNDLED / "default"


class ThemeError(Exception):
    """A theme that can't be loaded. An exception, not an exit: `loop` keeps the site as it is."""


def bundled() -> list[Path]:
    """The bundled themes a site can use: those with a home page (base has none of its own)."""
    return sorted(p for p in BUNDLED.iterdir() if (p / "templates" / "home.html").is_file())


@dataclass(frozen=True)
class Extra:
    """A page of the theme's own, such as "how it works"."""

    url: str
    label: str
    template: str
    description: str = ""  # for search results and link previews
    sources: tuple[str, ...] = ()  # the sources the page is written for ("drive", "nextcloud"); () for any


@dataclass(frozen=True)
class Theme:
    dirs: tuple[Path, ...]  # this theme, then each theme it extends
    language: str
    months: tuple[str, ...]
    home: str  # the first breadcrumb
    extras: dict[str, Extra]  # pages of the theme's own
    strings: dict[str, str] = field(default_factory=dict)  # the words of the templates
    stylesheets: tuple[str, ...] = ()  # in load order: the parent's first

    @property
    def dir(self) -> Path:
        return self.dirs[0]

    @property
    def templates(self) -> list[Path]:
        """Template folders in lookup order: this theme's first."""
        return [d / "templates" for d in self.dirs if (d / "templates").is_dir()]

    def static_files(self) -> dict[str, bytes]:
        """Every static file by its path under /static/; this theme's replace its parent's."""
        files: dict[str, bytes] = {}
        for static in (d / "static" for d in reversed(self.dirs)):
            if static.is_dir():
                files |= {p.relative_to(static).as_posix(): p.read_bytes() for p in _files(static)}
        return files

    def fingerprint(self) -> str:
        """Changes when any file of the theme or what it extends changes: a cue to rebuild."""
        stamp = hashlib.sha1()
        for d in self.dirs:
            for p in sorted(_files(d)):
                stat = p.stat()
                stamp.update(f"{p}:{stat.st_size}:{stat.st_mtime_ns}".encode())
        return stamp.hexdigest()

    def date(self, value: datetime) -> str:
        return f"{value.day} {self.months[value.month - 1]} {value.year}"


def _merge(base: dict, over: dict) -> dict:
    """`over` on top of `base`, table by table."""
    merged = dict(base)
    for key, value in over.items():
        both_tables = isinstance(value, dict) and isinstance(merged.get(key), dict)
        merged[key] = _merge(merged[key], value) if both_tables else value
    return merged


def _files(folder: Path) -> list[Path]:
    """The files in a folder and its subfolders, without hidden ones (.DS_Store and the like)."""
    return [
        p for p in folder.rglob("*") if p.is_file() and not any(s.startswith(".") for s in p.relative_to(folder).parts)
    ]


def _resolve(name: str, relative_to: Path) -> Path:
    for candidate in (relative_to / name, BUNDLED / name):
        if (candidate / "theme.toml").is_file():
            return candidate.resolve()
    raise ThemeError(f"Theme {relative_to} extends {name!r}, which is neither a folder nor a bundled theme")


def load(path: Path) -> Theme:
    dirs: list[Path] = []
    chain: list[dict] = []  # each theme's own theme.toml, child first
    current: Path | None = Path(path).resolve()
    while current:
        if current in dirs:
            raise ThemeError(f"Theme {path} extends itself through {current}")
        try:
            raw = tomllib.loads((current / "theme.toml").read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ThemeError(f"No theme at {current} (THEME_DIR): it needs a theme.toml") from None
        except tomllib.TOMLDecodeError as e:
            raise ThemeError(f"{current / 'theme.toml'}: {e}") from None
        dirs.append(current)
        chain.append(raw)
        current = _resolve(raw["extends"], current) if "extends" in raw else None

    merged: dict = {}
    stylesheets: list[str] = []
    for raw in reversed(chain):  # the furthest ancestor first
        merged = _merge(merged, {k: v for k, v in raw.items() if k not in ("extends", "stylesheets")})
        stylesheets += [s for s in raw.get("stylesheets", []) if s not in stylesheets]
    try:
        return _theme(dirs, merged, stylesheets)
    except (KeyError, TypeError) as e:  # a setting missing, or one an extra page doesn't take
        raise ThemeError(f"Theme {path}: a setting is missing or unknown: {e}") from None


def _theme(dirs: list[Path], merged: dict, stylesheets: list[str]) -> Theme:
    return Theme(
        dirs=tuple(dirs),
        language=merged["language"],
        months=tuple(merged["months"]),
        home=merged["home"],
        # An empty template switches off an extra page the theme inherits.
        extras={
            key: Extra(**{**value, "sources": tuple(value.get("sources", ()))})
            for key, value in merged.get("extras", {}).items()
            if value.get("template", True)
        },
        strings=merged.get("strings", {}),
        stylesheets=tuple(stylesheets),
    )
