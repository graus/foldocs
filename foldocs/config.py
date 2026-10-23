"""Settings, read from the environment, and the conventions for the site's folder."""

import logging
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .sources import Entry, provider_for
from .theme import DEFAULT as DEFAULT_THEME

log = logging.getLogger(__name__)

# Two names (ignoring case) make a Doc no page of its own. An "intro" opens the page of the folder
# it is in: in the root folder, the home page (its links are the site's contact links); in any
# other, that section's page. A "design" in the root folder sets the site's colors and fonts
# (design.py). Every other Doc in the root folder is a page in the menu.
SPECIAL_NAMES = ("intro", "design")


def special_name(name: str) -> str | None:
    """ "intro" or "design" for a Doc with that name, else None."""
    name = name.strip().lower()
    return name if name in SPECIAL_NAMES else None


def role(entry: Entry) -> str | None:
    """A Doc's special role where it is: "intro" in any folder, "design" in the root folder only."""
    name = special_name(entry.stem) if entry.kind == "doc" else None
    return name if name == "intro" or (name == "design" and not entry.path) else None


@dataclass(frozen=True)
class Settings:
    folder_url: str  # the shared folder's link, as the service's Share dialog gives it
    keys: dict[str, str]  # the settings the service needs besides the link, such as GOOGLE_API_KEY
    data_dir: Path
    site_url: str
    author: str
    noindex: bool  # keep the site out of search results: a robots noindex tag on every page
    theme_dir: Path = DEFAULT_THEME
    output: str = "public"  # the folder in data_dir the site is built into
    original_url: str | None = None  # for a variant: the site it is a variant of
    folder_password: str = ""  # the share link's password, for a service whose links can have one
    timezone: str = "Europe/Amsterdam"  # for dates: a photo's camera time, a date in a file name, "now"

    @property
    def site_name(self) -> str:  # the host name, for social previews
        return urlparse(self.site_url).hostname or self.site_url

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def public_dir(self) -> Path:
        return self.data_dir / self.output

    @property
    def media_dir(self) -> Path:
        return self.public_dir / "media"


VARIANT_NAME = re.compile(r"[a-z0-9-]+")  # what a host name's first part can be
_skipped: set[Path] = set()  # variants already warned about: the loop asks for them every round


def variants(settings: Settings) -> list[Settings]:
    """Variants: other designs of the same site. Each theme in the theme's variants/ folder is
    built with the same content into data/variants/<name>, for <name>.<the site's host>."""
    folder = settings.theme_dir / "variants"
    if settings.original_url or not folder.is_dir():
        return []
    scheme, host = settings.site_url.split("://", 1)
    themes = [theme for theme in sorted(folder.iterdir()) if (theme / "theme.toml").is_file()]
    for theme in themes:
        if not VARIANT_NAME.fullmatch(theme.name) and theme not in _skipped:
            _skipped.add(theme)
            log.warning("Skipping the variant %r: a name of a-z, 0-9 and - only, to be a host name", theme.name)
    return [
        replace(
            settings,
            theme_dir=theme,
            site_url=f"{scheme}://{theme.name}.{host}",
            output=f"variants/{theme.name}",
            original_url=settings.site_url,
        )
        for theme in themes
        if VARIANT_NAME.fullmatch(theme.name)
    ]


REQUIRED = ("FOLDER_URL", "SITE_URL", "AUTHOR")


def load() -> Settings:
    """The settings, from the environment. The folder is one link, whichever service it is in:
    FOLDER_URL, as copied from the service's Share dialog; the service is told from the address."""
    missing = [key for key in REQUIRED if not os.environ.get(key)]
    folder = os.environ.get("FOLDER_URL", "").strip()
    try:
        provider = provider_for(folder) if folder else None
        if provider:
            provider.check(folder)
    except ValueError as e:
        raise SystemExit(f"FOLDER_URL: {e}") from None
    missing += [key for key in (provider.keys if provider else ()) if not os.environ.get(key)]
    if missing:
        raise SystemExit(f"Missing settings: {', '.join(missing)} (see .env.example)")
    password = os.environ.get("FOLDER_PASSWORD", "")
    if password and not provider.password:
        log.warning("FOLDER_PASSWORD is set, but a %s link has no password: it is not used", provider.label)
    timezone = os.environ.get("TIMEZONE") or Settings.timezone
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise SystemExit(f"TIMEZONE: not a time zone, such as Europe/Amsterdam: {timezone}") from None
    site_url = os.environ["SITE_URL"].rstrip("/")
    if urlparse(site_url).scheme not in ("http", "https") or not urlparse(site_url).hostname:
        raise SystemExit(f"SITE_URL: not a web address with http:// or https:// in front: {site_url}")
    return Settings(
        folder_url=folder,
        keys={key: os.environ[key] for key in provider.keys},
        data_dir=Path(os.environ.get("DATA_DIR", "data")),
        site_url=site_url,
        author=os.environ["AUTHOR"],
        noindex=os.environ.get("NOINDEX", "").lower() in ("1", "true", "yes"),
        theme_dir=Path(os.environ.get("THEME_DIR") or DEFAULT_THEME),
        folder_password=password if provider.password else "",
        timezone=timezone,
    )
