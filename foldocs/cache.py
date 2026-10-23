"""The local cache of the folder: what the sync writes and the build reads.

  cache/manifest.json        every file the site shows (or will, once it has settled): names,
                             folders and dates, as the source listed them
  cache/folders.json         every folder, with its ID where the source has one
  cache/content/<id>.json    per file, its processed content: a document's HTML, a photo's sizes

A file's images are not in the cache but in the site's media folder, which the sync writes too.
"""

import hashlib
import json
import os
import re
from pathlib import Path

FILE_ID = re.compile(r"[\w-]+")  # as every source gives them: letters, digits, - and _


class Cache:
    def __init__(self, folder: Path) -> None:
        self.manifest_path = folder / "manifest.json"
        self.folders_path = folder / "folders.json"
        self.content_dir = folder / "content"

    def manifest(self) -> list[dict] | None:
        """The files, or None before the first sync."""
        return read_json(self.manifest_path)

    def folders(self) -> list[dict]:
        return read_json(self.folders_path) or []

    def content_path(self, file_id: str) -> Path:
        if not FILE_ID.fullmatch(file_id):  # it names a file here: never a path
            raise ValueError(f"Not a file ID: {file_id!r}")
        return self.content_dir / f"{file_id}.json"

    def content(self, file_id: str) -> dict | None:
        return read_json(self.content_path(file_id))

    def published(self) -> set[str]:
        """The IDs of the files with content: those the site shows."""
        return {path.stem for path in self.content_dir.glob("*.json")}


def write_json(path: Path, data) -> None:
    """Write atomically: a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def media_name(file_id: str) -> str:
    """The folder a file's images are served from. Not its file ID: the folder is public, and
    with the ID anyone could open the original (in Drive), camera metadata and all, and find the
    folder it is in. A hash of the ID can't be turned back into it."""
    return hashlib.sha256(file_id.encode()).hexdigest()[:16]
