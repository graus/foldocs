"""Google Drive as a source: what it may fetch, what it does on a folder it can't see, its key."""

import io

import pytest
import requests

from foldocs import sources
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
            Drive.fetch(url)


def test_a_redirect_away_from_google_is_not_followed(monkeypatch):
    replies = [response(302, location="https://example.org/x.png")]
    monkeypatch.setattr(drive_module.requests, "get", lambda url, **_: replies.pop(0))
    with pytest.raises(ValueError):
        Drive.fetch("https://lh3.googleusercontent.com/x")


def test_a_redirect_within_google_is_followed(monkeypatch):
    replies = [response(302, location="/y"), response(200, b"image")]
    monkeypatch.setattr(drive_module.requests, "get", lambda url, **_: replies.pop(0))
    assert Drive.fetch("https://lh3.googleusercontent.com/x") == b"image"


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


def test_an_unreachable_root_folder_stops_the_sync_instead_of_emptying_the_site():
    class Unshared:  # what Drive answers about a folder that is not shared publicly
        ok, status_code, text = False, 404, "File not found: root"

        def raise_for_status(self):
            raise requests.HTTPError("404 Client Error: Not Found")

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
