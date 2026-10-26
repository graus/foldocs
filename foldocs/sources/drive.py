"""Read-only access to a public Google Drive folder, through four calls of the Drive REST API
(list a folder, get a file, export a Doc, download a file). The folder is public ("anyone with
the link can view"), as everything in it is on the site anyway, so an API key is all it takes:
no account, no login, no key file."""

import io
import json
import logging
import re
import zipfile
from urllib.parse import parse_qs, urljoin, urlparse

import requests

from ..formats import DOC, described, format_for
from . import TIMEOUT, DocExport, Entry, Folder, Provider, body, counted

log = logging.getLogger(__name__)

FIELDS = "id,name,mimeType,createdTime,modifiedTime,description,imageMediaMetadata(time)"
API = "https://www.googleapis.com/drive/v3/files"
PAGE_SIZE = 1000  # files per listing, the most the API gives
REDIRECTS_MAX = 3  # for an image a Doc links to


FOLDER_URL = "https://drive.google.com/drive/folders/"
FOLDER = "application/vnd.google-apps.folder"
HOSTS = ("drive.google.com", "docs.google.com")
# A file's or folder's path on those hosts: a Doc, Sheet or Slides file, any other file, or a folder,
# for a person signed in to more than one account (/u/1/) or in an organization's (/a/<domain>/) too
FILE_PATH = re.compile(
    r"/(?:(?:document|spreadsheets|presentation|file)(?:/u/\d+)?/d|drive/(?:u/\d+/)?folders)/([\w-]+)"
)


def folder_id(folder_url: str) -> str:
    """The folder's ID, from its link as Drive's Share dialog gives it, or the ID itself."""
    m = re.search(r"/folders/([\w-]+)", folder_url) or re.search(r"[?&]id=([\w-]+)", folder_url)
    if m:
        return m[1]
    if re.fullmatch(r"[\w-]+", folder_url):
        return folder_url
    raise ValueError(f"Not a Google Drive folder link: {folder_url}")


def _is_drive(folder_url: str) -> bool:
    """A Drive link, or a bare folder ID."""
    if not folder_url.startswith(("http://", "https://")):
        return True
    return (urlparse(folder_url).hostname or "") in HOSTS


def unzip(data: bytes) -> DocExport:
    """A Doc's zip export: its HTML, and the images bundled with it by the src they have there."""
    z = zipfile.ZipFile(io.BytesIO(data))
    html_name = next(n for n in z.namelist() if n.endswith(".html"))
    files = {n: z.read(n) for n in z.namelist() if n != html_name}
    return DocExport(z.read(html_name).decode("utf-8"), files)


class Drive:
    kind = "drive"
    max_items: int | None = None
    max_bytes: int | None = None

    def __init__(self, api_key: str, folder_id: str):
        self.folder_id = folder_id
        self.url = FOLDER_URL + folder_id
        self.embed = f"https://drive.google.com/embeddedfolderview?id={folder_id}#list"
        self.http = requests.Session()
        # In a header, not the URL: an error message quotes the URL, and would put the key in logs.
        self.http.headers["X-Goog-Api-Key"] = api_key

    def _get(self, path: str = "", **params) -> bytes:
        """A GET on the files API; an error status raises requests.HTTPError."""
        return body(self.http.get(API + path, params=params, timeout=TIMEOUT, stream=True), self.max_bytes)

    def _children(self, folder_id: str) -> list[dict]:
        """A folder's files and folders, page by page."""
        files, token = [], None
        while True:
            page = json.loads(
                self._get(
                    q=f"'{folder_id}' in parents and trashed = false",
                    fields=f"nextPageToken,files({FIELDS})",
                    pageSize=PAGE_SIZE,
                    **({"pageToken": token} if token else {}),
                )
            )
            files += counted(self, page.get("files", []))
            token = page.get("nextPageToken")
            if not token:
                return files

    def walk(self) -> tuple[list[Entry], list[Folder]]:
        # Listing a folder that is not shared publicly returns nothing rather than an error,
        # which would read as "everything was deleted". Fail instead, and leave the site as it is.
        self._get(f"/{self.folder_id}", fields="id")
        self.listed = 0
        return self._walk(self.folder_id, [])

    def _walk(self, root_id: str, path: list[str]) -> tuple[list[Entry], list[Folder]]:
        """All documents and images under a folder, and the folders they are in; depth first."""
        entries, folders = [], []
        for f in self._children(root_id):
            if f["mimeType"] == FOLDER:
                folders.append(Folder(path + [f["name"]], f["id"]))
                sub_entries, sub_folders = self._walk(f["id"], path + [f["name"]])
                entries += sub_entries
                folders += sub_folders
            elif found := format_for(f["mimeType"], f["name"]):
                entries.append(
                    Entry(
                        id=f["id"],
                        name=f["name"],
                        mime=f["mimeType"],
                        path=path,
                        created=f["createdTime"],
                        modified=f["modifiedTime"],
                        description=f.get("description", ""),
                        taken=f.get("imageMediaMetadata", {}).get("time"),
                        format=found.name,
                    )
                )
            else:
                where = "/".join(path + [f["name"]])
                log.debug("Skipping %s (%s): not %s", where, f["mimeType"], described())
        return entries, folders

    def export_doc(self, entry: Entry) -> DocExport:
        """A Doc as HTML. The zip export bundles its images; Docs over Google's 10 MB export
        limit fall back to plain HTML, whose images are then fetched by URL."""
        resp = self.http.get(
            f"{API}/{entry.id}/export", params={"mimeType": "application/zip"}, timeout=TIMEOUT, stream=True
        )
        if not resp.ok:
            with resp:  # an error says why at its start
                said = next(resp.iter_content(1 << 16), b"")
            if b"exportSizeLimitExceeded" in said:
                log.info("Zip export of %s refused (too large), falling back to HTML", entry.id)
                return DocExport(self._get(f"/{entry.id}/export", mimeType="text/html").decode("utf-8"))
            resp.raise_for_status()
        return unzip(body(resp, self.max_bytes))

    def download(self, entry: Entry) -> bytes:
        resp = self.http.get(f"{API}/{entry.id}", params={"alt": "media"}, timeout=TIMEOUT, stream=True)
        return body(resp, self.max_bytes)

    @staticmethod
    def file_url(entry: Entry) -> str:
        if entry.mime == DOC:
            return f"https://docs.google.com/document/d/{entry.id}/edit"
        return f"https://drive.google.com/file/d/{entry.id}/view"

    @staticmethod
    def folder_url(folder: Folder) -> str:
        return FOLDER_URL + folder.id if folder.id else ""

    @staticmethod
    def file_id(url: str) -> str | None:
        """The ID in a link to a file or folder on Drive or in Google Docs, as Docs writes a link to
        another Doc (docs.google.com/document/d/<ID>/edit) or Drive's Share dialog gives one."""
        parts = urlparse(url)
        if parts.scheme not in ("http", "https") or parts.hostname not in HOSTS:
            return None
        if m := FILE_PATH.search(parts.path):
            return m[1]
        if parts.path in ("/open", "/uc"):
            return next(iter(parse_qs(parts.query).get("id", [])), None)
        return None

    def fetch(self, url: str) -> bytes:
        """An image a plain-HTML Doc export links to: only from Google's image servers, redirects
        included."""
        for _ in range(REDIRECTS_MAX + 1):
            parts = urlparse(url)
            host = parts.hostname or ""
            from_google = host == "googleusercontent.com" or host.endswith(".googleusercontent.com")
            if parts.scheme != "https" or not from_google:
                raise ValueError(f"Not fetching {url}: images are only fetched from googleusercontent.com")
            resp = requests.get(url, timeout=TIMEOUT, stream=True, allow_redirects=False)
            if not resp.is_redirect:
                return body(resp, self.max_bytes)
            url = urljoin(url, resp.headers["Location"])
            resp.close()
        raise ValueError(f"Not fetching {url}: too many redirects")


PROVIDER = Provider(
    kind="drive",
    label="Google Drive",
    matches=_is_drive,
    check=folder_id,
    # Building a site from the cache needs no key: only the sync asks Drive for anything.
    open=lambda settings: Drive(settings.keys.get("GOOGLE_API_KEY", ""), folder_id(settings.folder_url)),
    keys=("GOOGLE_API_KEY",),
)
