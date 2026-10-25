"""Read-only access to a Nextcloud folder through its public share link ("Share link", read only),
over the public WebDAV endpoint: a PROPFIND to list a folder, a GET to download a file. The share
token is the user name and the share's password, if it has one, the password: no account needed.

Nextcloud keeps documents as files, so pages are Word files (.docx), read as Docs are. What Drive
has and a share doesn't: file descriptions, so photos get no captions. A file's creation time is
used where Nextcloud knows it, else its upload time, else its last change.
"""

import hashlib
import logging
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote, unquote, urlencode, urlparse

import requests

from ..formats import described, format_for
from ..xmlsafe import untrusted_xml
from . import TIMEOUT, Entry, Folder, Provider, body, counted

log = logging.getLogger(__name__)

# XML namespaces, names rather than addresses: Nextcloud began as a fork of ownCloud and kept
# its oc: names for the older properties; nc: is for those it added since (the upload time).
NS = {"d": "DAV:", "oc": "http://owncloud.org/ns", "nc": "http://nextcloud.org/ns"}
PROPFIND = """<?xml version="1.0"?>
<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">
  <d:prop>
    <d:resourcetype/><d:getcontenttype/><d:getlastmodified/>
    <oc:fileid/><nc:creation_time/><nc:upload_time/>
  </d:prop>
</d:propfind>"""
ODT = "application/vnd.oasis.opendocument.text"
EPOCH = "1970-01-01T00:00:00Z"  # for a file with no date at all
warned: set[str] = set()  # the OpenDocument files already warned about: `loop` lists every minute


def parse_share(url: str) -> tuple[str, str]:
    """The server's address and the share token, from a link like https://host/s/<token>
    (or https://host/nextcloud/index.php/s/<token> for a server in a subfolder)."""
    parts = urlparse(url)
    prefix, sep, rest = parts.path.partition("/s/")
    token = rest.split("/")[0]
    if parts.scheme not in ("http", "https") or not sep or not token:
        raise ValueError(f"Not a Nextcloud share link: {url}")
    return f"{parts.scheme}://{parts.netloc}{prefix.removesuffix('/index.php')}", token


def _rfc3339(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _http_date(value: str) -> str | None:
    """getlastmodified, an HTTP date, as RFC 3339; None if missing or unreadable."""
    try:
        return _rfc3339(parsedate_to_datetime(value))
    except (TypeError, ValueError):
        return None


def _timestamp(value: str) -> str | None:
    """creation_time or upload_time, Unix seconds, as RFC 3339; None if missing, unreadable or
    0, which is how Nextcloud says it doesn't know (on most storage it records no creation time)."""
    try:
        seconds = int(value)
        return _rfc3339(datetime.fromtimestamp(seconds, UTC)) if seconds > 0 else None
    except (ValueError, OverflowError, OSError):
        return None


def _in_share(path: str) -> str:
    """A WebDAV path as a path within the share: what follows /public.php/webdav, unquoted. A proxy
    that rewrites the prefix in front of it changes nothing."""
    return unquote(path.split("/public.php/webdav", 1)[-1]).rstrip("/")


class Nextcloud:
    kind = "nextcloud"
    embed = ""  # Nextcloud forbids showing its pages in another site's frame
    max_items: int | None = None
    max_bytes: int | None = None

    def __init__(self, share_url: str, password: str = ""):
        self.base, self.token = parse_share(share_url)
        self.url = f"{self.base}/s/{self.token}"
        # The legacy public endpoint: still served by current versions, and by every older one.
        self.dav = f"{self.base}/public.php/webdav"
        self.http = requests.Session()
        self.http.auth = (self.token, password)

    def _path_url(self, path: list[str]) -> str:
        return self.dav + quote("/" + "/".join(path))

    def _children(self, path: list[str]) -> list[tuple[str, dict[str, str]]]:
        """The files and folders in a folder, by name, with their properties."""
        url = self._path_url(path)
        resp = self.http.request(
            "PROPFIND",
            url,
            data=PROPFIND,
            headers={"Depth": "1", "Content-Type": "application/xml"},
            timeout=TIMEOUT,
            stream=True,
        )
        own = _in_share(urlparse(url).path)
        children = []
        for response in untrusted_xml(body(resp, self.max_bytes)).findall("d:response", NS):
            href = urlparse(response.findtext("d:href", "", NS)).path.rstrip("/")
            if _in_share(href) == own:
                continue  # the folder itself
            props = {}
            for propstat in response.findall("d:propstat", NS):
                if " 200 " not in propstat.findtext("d:status", "", NS):
                    continue  # the properties this server doesn't have
                for prop in propstat.findall("d:prop/*", NS):
                    tag = prop.tag.split("}")[-1]
                    if tag == "resourcetype":
                        props[tag] = "folder" if prop.find("d:collection", NS) is not None else ""
                    else:
                        props[tag] = (prop.text or "").strip()
            children.append((unquote(href.rsplit("/", 1)[-1]), props))
        # A server can answer anything: a folder inside itself, without end, or a million files.
        return counted(self, children)

    def walk(self) -> tuple[list[Entry], list[Folder]]:
        # An unknown or expired share answers 401 or 404, which raises: the site stays as it is.
        self.listed = 0
        return self._walk([])

    def _walk(self, path: list[str]) -> tuple[list[Entry], list[Folder]]:
        """All documents and images under a folder, and the folders they are in; depth first."""
        entries, folders = [], []
        for name, props in self._children(path):
            # The file ID survives renames and moves. It names the file's cache entry, so only digits,
            # what Nextcloud sends, are taken: from another server "../x" would write outside the cache.
            # A file without one is known by its path, hashed.
            file_id = props.get("fileid", "")
            if not (file_id.isascii() and file_id.isdigit()):
                file_id = hashlib.sha256("/".join(path + [name]).encode()).hexdigest()[:24]
            if props.get("resourcetype") == "folder":
                folders.append(Folder(path + [name], file_id))
                sub_entries, sub_folders = self._walk(path + [name])
                entries += sub_entries
                folders += sub_folders
                continue
            mime, where = props.get("getcontenttype", ""), "/".join(path + [name])
            if mime == ODT:
                # Collabora's default format, easily taken for a Word file: say why the page is missing.
                if file_id not in warned:
                    warned.add(file_id)
                    log.warning("Skipping %s: an OpenDocument file; save it as Word (.docx) to publish it", where)
                continue
            if not (found := format_for(mime, name)):
                log.debug("Skipping %s (%s): not %s", where, mime, described())
                continue
            # A date the server leaves out or garbles counts as unknown: one file never stops the sync.
            modified = _http_date(props.get("getlastmodified", ""))
            created = _timestamp(props.get("creation_time", "")) or _timestamp(props.get("upload_time", ""))
            modified = modified or created or EPOCH
            created = created or modified
            entries.append(
                Entry(
                    id=file_id, name=name, mime=mime, path=path, created=created, modified=modified, format=found.name
                )
            )
        return entries, folders

    def download(self, entry: Entry) -> bytes:
        resp = self.http.get(self._path_url(entry.path + [entry.name]), timeout=TIMEOUT, stream=True)
        return body(resp, self.max_bytes)

    def _folder_page(self, path: list[str]) -> str:
        return f"{self.url}?{urlencode({'dir': '/' + '/'.join(path)})}"

    def file_url(self, entry: Entry) -> str:
        """The folder the file is in: a share page can't be opened on one file in every version."""
        return self._folder_page(entry.path)

    def folder_url(self, folder: Folder) -> str:
        return self._folder_page(folder.path)


def _is_share(folder_url: str) -> bool:
    try:
        parse_share(folder_url)
        return True
    except ValueError:
        return False


PROVIDER = Provider(
    kind="nextcloud",
    label="Nextcloud",
    matches=_is_share,
    check=parse_share,
    open=lambda settings: Nextcloud(settings.folder_url, settings.folder_password),
    password=True,
)
