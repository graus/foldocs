"""Google Drive as a source: what it may fetch, what it does on a folder it can't see, its key."""

import io
import json

import pytest
import requests

from foldocs import sources
from foldocs.formats import DOC
from foldocs.sources import drive as drive_module
from foldocs.sources.drive import Drive


def response(status=200, content=b"", location=None) -> requests.Response:
    resp = requests.Response()
    resp.status_code, resp.raw = status, io.BytesIO(content)
    if location:
        resp.headers["Location"] = location
    return resp


def test_images_are_only_fetched_from_google():
    for url in [
        "http://lh3.googleusercontent.com/x",
        "https://example.org/x.png",
        "https://googleusercontent.com.evil.org/x",
    ]:
        with pytest.raises(ValueError):
            Drive("api-key", "root").fetch(url)


def test_a_redirect_away_from_google_is_not_followed(monkeypatch):
    replies = [response(302, location="https://example.org/x.png")]
    monkeypatch.setattr(drive_module.requests, "get", lambda url, **_: replies.pop(0))
    with pytest.raises(ValueError):
        Drive("api-key", "root").fetch("https://lh3.googleusercontent.com/x")


def test_a_redirect_within_google_is_followed(monkeypatch):
    replies = [response(302, location="/y"), response(200, b"image")]
    monkeypatch.setattr(drive_module.requests, "get", lambda url, **_: replies.pop(0))
    assert Drive("api-key", "root").fetch("https://lh3.googleusercontent.com/x") == b"image"


def test_an_error_status_raises_and_the_response_is_closed():
    resp = response(404, b"gone")
    with pytest.raises(requests.HTTPError):
        sources.body(resp)
    assert resp.raw.closed


def test_a_file_too_large_to_hold_is_refused(monkeypatch):
    monkeypatch.setattr(sources, "DOWNLOAD_MAX", 10)
    with pytest.raises(ValueError, match="too large"):
        sources.body(response(content=b"x" * 11))
    assert sources.body(response(content=b"x" * 10)) == b"x" * 10


def test_a_source_can_be_given_a_lower_limit():
    with pytest.raises(sources.TooLarge):
        sources.body(response(content=b"x" * 11), limit=10)


def test_a_folder_listing_more_than_max_items_is_refused_while_it_is_listed():
    class Session:
        def __init__(self):
            self.listings = 0

        def get(self, url, params=None, **_):
            if "q" not in params:
                return response(content=b'{"id": "root"}')
            self.listings += 1
            files = [{"id": f"f{self.listings}{i}", "name": "Sub", "mimeType": drive_module.FOLDER} for i in range(3)]
            return response(content=json.dumps({"files": files}).encode())

    drive = Drive("api-key", "root")
    drive.http, drive.max_items = Session(), 5
    with pytest.raises(sources.TooLarge):
        drive.walk()  # every folder holds three more: without the limit, it never ends
    assert drive.http.listings == 2


def test_a_doc_too_large_to_zip_is_exported_as_html_under_the_same_limit():
    refused = b'{"error": {"errors": [{"reason": "exportSizeLimitExceeded"}]}}'
    replies = [response(403, refused), response(200, b"<p>Hi</p>")]

    class Session:
        def get(self, url, params=None, **_):
            return replies.pop(0)

    drive = Drive("api-key", "root")
    drive.http = Session()
    assert drive.export_doc(sources.Entry("d", "Doc", DOC, [], "", "")).html == "<p>Hi</p>"
    replies[:] = [response(403, refused), response(200, b"x" * 11)]
    drive.max_bytes = 10
    with pytest.raises(sources.TooLarge):
        drive.export_doc(sources.Entry("d", "Doc", DOC, [], "", ""))


def test_an_unreachable_root_folder_stops_the_sync_instead_of_emptying_the_site():
    class Unshared:  # what Drive answers about a folder that is not shared publicly
        ok, status_code, text = False, 404, "File not found: root"

        def raise_for_status(self):
            raise requests.HTTPError("404 Client Error: Not Found")

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    class Session:
        def get(self, url, params=None, **_):
            assert "q" not in (params or {}), "must not list a folder it could not see"
            return Unshared()

    drive = Drive("api-key", "root")
    drive.http = Session()
    with pytest.raises(requests.HTTPError):
        drive.walk()


def test_the_api_key_is_sent_in_a_header_never_in_the_url():
    """An error message quotes the URL; a key in it would end up in the logs."""
    drive = Drive("secret-key", "root")
    assert drive.http.headers["X-Goog-Api-Key"] == "secret-key"
    assert not drive.http.params


def test_a_link_to_a_doc_or_file_names_its_id():
    for url, found in [
        ("https://docs.google.com/document/d/1AbC_d-E/edit?usp=sharing", "1AbC_d-E"),
        ("https://docs.google.com/spreadsheets/d/S1/edit#gid=0", "S1"),
        ("https://drive.google.com/file/d/XyZ/view", "XyZ"),
        ("https://drive.google.com/open?id=Q1", "Q1"),
        ("https://docs.google.com/document/u/1/d/D2/edit", "D2"),  # signed in to more than one account
        ("https://docs.google.com/a/example.com/document/d/D3/edit", "D3"),  # an organization's
        ("https://drive.google.com/uc?id=U1&export=download", "U1"),
        ("https://drive.google.com/drive/folders/F1", "F1"),
        ("https://drive.google.com/drive/u/0/folders/F2", "F2"),
        ("https://example.com/document/d/not-google", None),
        ("https://drive.google.com/drive/my-drive", None),
    ]:
        assert Drive.file_id(url) == found, url
