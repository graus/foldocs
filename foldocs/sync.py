"""Mirror the site's folder (in Google Drive or Nextcloud) into the local cache, fetching only what changed.

The cache holds a manifest of the tree (names, folders, dates: cheap to list every time)
and, per file, its processed content, which is only redone when the file's modification time
changes. Images are written straight into the site's media folder.

Documents save while you type (Google Docs, Nextcloud's editors), so a file can be left to
settle first: a change is only picked up once the file has been untouched for `settle` seconds,
so the site never shows a document halfway through an edit. Until then the previous version
stays (or, for a new file, nothing yet).
"""

import hashlib
import logging
import shutil
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import requests

from . import convert, design, images
from .cache import Cache, media_name, write_json
from .config import Settings, role
from .formats import Document, Reading
from .sources import Entry, Folder, Source, open_source

log = logging.getLogger(__name__)

# Bump when processing changes, to redo every file on the next sync.
VERSION = 16

# Files that couldn't be processed, and how they were then (their modification time): one is
# tried again once it changes, not every minute. A failure to reach the service is tried again.
_failed: dict[str, str] = {}


def _passing(e: Exception) -> bool:
    """Whether trying again later may work: the service unreachable, slow or busy, but not a
    refusal (a file whose owner turned off downloads, one that is gone)."""
    if isinstance(e, requests.HTTPError) and e.response is not None:
        status = e.response.status_code
        return not 400 <= status < 500 or status in (408, 429)
    return isinstance(e, requests.RequestException)


def sync(settings: Settings, source: Source | None = None, settle: int = 0) -> bool:
    """Bring the cache up to date with the source; files changed less than `settle` seconds ago wait.
    Returns whether anything the site shows has changed, so the caller knows to rebuild."""
    source = source or open_source(settings)
    cache = Cache(settings.cache_dir)
    listed, folders = source.walk()
    # Every file by its path, hidden ones too, for a document that shows one; of two with one name
    # in a folder (Drive allows it), the newest.
    tree = {(*e.path, e.name): e for e in sorted(listed, key=lambda e: e.modified)}
    entries, folders = [e for e in listed if not e.hidden], [f for f in folders if not f.hidden]
    previous = cache.manifest() or []
    if not entries and previous:
        # Between deleting everything and uploading it again, the folder is empty for a moment;
        # an empty site is never what that means. To take a site down, stop it.
        log.warning("The folder is empty: keeping the site as it is until files are back")
        return False
    changed, waiting = _update(settings, cache, source, entries, tree, settle)
    held = _held(cache, entries, previous) if waiting else []
    removed = _prune(settings, cache, {e.id for e in entries} | {e["id"] for e in held})
    moved = _save_tree(cache, [asdict(e) for e in entries] + held, folders, previous)
    log.info(
        "Synced %d files: %d updated, %d removed, %d settling%s",
        len(entries), changed, removed, waiting, f", {len(held)} gone but kept until then" if held else "",
    )  # fmt: skip
    return bool(changed or removed or moved)


Tree = dict[tuple[str, ...], Entry]  # every file in the folder, by its folders' names and its own


def _update(
    settings: Settings, cache: Cache, source: Source, entries: list[Entry], tree: Tree, settle: int
) -> tuple[int, int]:
    """Process every file that changed and has settled: (how many were, how many still settle)."""
    now = datetime.now(UTC)
    changed = waiting = 0
    for entry in entries:
        cached = cache.content(entry.id)
        unchanged = cached and cached["modified"] == entry.modified and cached["version"] == VERSION
        # A design Doc is processed differently; moved into or out of the root folder (or
        # renamed) it is processed again, though the file itself didn't change.
        if unchanged and cached.get("role") == _processing_role(entry):
            continue
        if _failed.get(entry.id) == entry.modified:
            continue
        # A time in the future (a skewed clock on the uploading machine) counts as settled.
        if settle and 0 <= (now - datetime.fromisoformat(entry.modified)).total_seconds() < settle:
            waiting += 1
            continue
        log.info("Processing %s", "/".join(entry.path + [entry.name]))
        name = media_name(entry.id)
        media, staging = settings.media_dir / name, settings.media_dir / f"{name}.new"
        shutil.rmtree(staging, ignore_errors=True)
        try:
            content = _process(Reading(entry, source, tree), staging)
        except Exception as e:
            # Keep the previous version, images included.
            log.exception("Could not process %s (%s)", entry.name, entry.id)
            shutil.rmtree(staging, ignore_errors=True)
            if not _passing(e):
                _failed[entry.id] = entry.modified
            continue
        _failed.pop(entry.id, None)
        shutil.rmtree(media, ignore_errors=True)
        if staging.exists():
            staging.rename(media)
        write_json(cache.content_path(entry.id), content)
        changed += 1
    return changed, waiting


def _held(cache: Cache, entries: list[Entry], previous: list[dict]) -> list[dict]:
    """While anything settles, the files that are gone but stay published: all but those a
    published file has taken the place of (the same name in the same folder). A file replaced by
    a new upload, which has a new ID, so stays until the new one is out, and the site never goes
    without it."""
    published = cache.published()
    out = {(tuple(e.path), e.name) for e in entries if e.id in published}
    ids = {e.id for e in entries}
    return [e for e in previous if e["id"] not in ids and (tuple(e["path"]), e["name"]) not in out]


def _prune(settings: Settings, cache: Cache, live: set[str]) -> int:
    """Remove the content and images of every file not in `live`; returns how many files."""
    removed = 0
    for file_id in cache.published() - live:
        cache.content_path(file_id).unlink()
        removed += 1
    live_media = {media_name(i) for i in live}
    if settings.media_dir.exists():
        for path in settings.media_dir.iterdir():
            if path.name not in live_media:
                shutil.rmtree(path, ignore_errors=True)
    return removed


def _save_tree(cache: Cache, manifest: list[dict], folders: list[Folder], previous: list[dict]) -> bool:
    """Write the manifest and the folders; returns whether the site shows them differently. A
    rename, move or new caption changes them without changing any content; an edit still
    settling changes only a modification time, which the site doesn't show yet."""
    published = cache.published()
    moved = _shape(previous, published) != _shape(manifest, published)
    write_json(cache.manifest_path, manifest)
    folder_list = sorted((asdict(f) for f in folders), key=lambda f: f["path"])
    moved |= cache.folders() != folder_list
    write_json(cache.folders_path, folder_list)
    return moved


def _shape(manifest: list[dict] | None, published: set[str]) -> list[dict]:
    """What the site shows of a manifest: its published files, without modification times, in an
    order of their own (a service may list a folder in a different order each time)."""
    shown = [{k: v for k, v in e.items() if k != "modified"} for e in manifest or [] if e["id"] in published]
    return sorted(shown, key=lambda e: e["id"])


def _processing_role(entry: Entry) -> str | None:
    """The one role that changes how a file is processed: "design", in the root folder."""
    return "design" if role(entry) == "design" else None


def _process(file: Reading, out_dir: Path) -> dict:
    """The file's content for the cache; its images go to out_dir, later served at /media/<name>/."""
    entry = file.entry
    # Media file names carry a version token, so they can be cached forever.
    token = hashlib.sha1(entry.modified.encode()).hexdigest()[:8]
    base = f"/media/{media_name(entry.id)}/"
    content = {"modified": entry.modified, "version": VERSION}

    if entry.kind == "image":
        info = images.process(file.download(), out_dir, f"photo-{token}")
        return content | {"image": info | {"base": base}}

    assert isinstance(entry.reader, Document)
    text = entry.reader.read(file)
    count = 0

    def image(src: str) -> dict | None:
        nonlocal count
        try:
            info = images.process(text.image(src), out_dir, f"{count + 1}-{token}")
        except Exception as e:  # left out: one image never keeps a document off the site
            if _passing(e):
                raise  # the whole document is tried again, rather than published without it
            log.warning("Could not publish image %s in %s: %s", src[:80], entry.name, e)
            return None
        count += 1
        return info | {"base": base}

    doc = convert.convert(text.html, image, images.attrs, text.title, text.subtitle)
    content["role"] = content_role = _processing_role(entry)
    if content_role == "design":
        # Its settings, and the fonts they name, fetched now: the build works from the cache only.
        content["design"] = design.parse(doc.html, entry.name)
        content["fonts"] = design.fonts(content["design"], out_dir, base)
    return content | {
        "title": doc.title,
        "subtitle": doc.subtitle,
        "html": doc.html,
        "text": doc.text,
        "cover": doc.images[0] if doc.images else None,
        "links": doc.links,
        "first": doc.first,
    }
