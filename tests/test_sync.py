"""The sync against a fake source: incremental, removing what is gone, safe on failure."""

import io
from datetime import UTC, datetime

import pytest
import requests
from PIL import Image

from foldocs import cache, config, sync
from foldocs.sources import DocExport, Entry

DOC = "application/vnd.google-apps.document"


def jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "orange").save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def no_failures_remembered():
    sync._failed.clear()


def http_error(status: int) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(f"{status}", response=resp)


class FakeDrive:
    def __init__(self, entries):
        self.entries, self.exports, self.fail, self.error = entries, [], set(), requests.ConnectionError
        self.image, self.src = jpeg(), "images/a.jpg"

    def walk(self):
        return self.entries, []

    def export_doc(self, entry):
        self.exports.append(entry.id)
        if entry.id in self.fail:
            raise self.error("failed")
        return DocExport(f'<html><body><p>Tekst <img src="{self.src}"></p></body></html>', {"images/a.jpg": self.image})

    def fetch(self, url):
        raise requests.Timeout("slow")


def entry(id, modified):
    return Entry(id=id, name=id, mime=DOC, path=["writing"], created="2026-01-01T00:00:00Z", modified=modified)


def settings(tmp_path):
    return config.Settings("root", {"GOOGLE_API_KEY": "api-key"}, tmp_path, "https://example.org", "Ann", False)


def test_only_changed_files_are_processed_and_gone_ones_removed(tmp_path):
    s = settings(tmp_path)
    drive = FakeDrive([entry("a", "t1"), entry("b", "t1")])
    sync.sync(s, drive)
    assert sorted(drive.exports) == ["a", "b"]

    drive.entries, drive.exports = [entry("a", "t2")], []
    sync.sync(s, drive)
    assert drive.exports == ["a"]  # b was unchanged, then gone
    assert not (s.cache_dir / "content" / "b.json").exists()
    assert not (s.media_dir / cache.media_name("b")).exists()


def test_a_failed_update_keeps_the_previous_version_and_its_images(tmp_path):
    s = settings(tmp_path)
    drive = FakeDrive([entry("a", "t1")])
    sync.sync(s, drive)
    media = s.media_dir / cache.media_name("a")
    before = sorted(p.name for p in media.iterdir())

    drive.entries, drive.fail = [entry("a", "t2")], {"a"}
    sync.sync(s, drive)
    assert cache.read_json(s.cache_dir / "content" / "a.json")["modified"] == "t1"
    assert sorted(p.name for p in media.iterdir()) == before
    assert not media.with_name(media.name + ".new").exists()


def test_a_file_that_cant_be_processed_is_tried_again_once_it_changes(tmp_path):
    s = settings(tmp_path)
    drive = FakeDrive([entry("a", "t1")])
    drive.fail, drive.error = {"a"}, ValueError
    sync.sync(s, drive)
    sync.sync(s, drive)
    assert drive.exports == ["a"]  # not every minute
    drive.entries, drive.fail = [entry("a", "t2")], set()
    sync.sync(s, drive)
    assert drive.exports == ["a", "a"]


def test_a_file_the_service_failed_to_send_is_tried_again(tmp_path):
    s = settings(tmp_path)
    drive = FakeDrive([entry("a", "t1")])
    drive.fail = {"a"}
    sync.sync(s, drive)
    sync.sync(s, drive)
    assert drive.exports == ["a", "a"]
    drive.exports, drive.error = [], lambda _: http_error(503)
    sync.sync(s, drive)
    sync.sync(s, drive)
    assert drive.exports == ["a", "a"]  # busy: try again


def test_a_file_the_service_refuses_waits_until_it_changes(tmp_path):
    """A 403 (its owner turned off downloads) or a 404 won't change by asking again."""
    s = settings(tmp_path)
    drive = FakeDrive([entry("a", "t1")])
    drive.fail, drive.error = {"a"}, lambda _: http_error(403)
    sync.sync(s, drive)
    sync.sync(s, drive)
    assert drive.exports == ["a"]


def test_a_document_whose_image_timed_out_is_tried_again_whole(tmp_path):
    s = settings(tmp_path)
    drive = FakeDrive([entry("a", "t1")])
    drive.src = "https://lh3.googleusercontent.com/x"
    sync.sync(s, drive)
    assert not (s.cache_dir / "content" / "a.json").exists()  # not published without its image
    sync.sync(s, drive)
    assert drive.exports == ["a", "a"]


def test_an_image_that_cant_be_read_is_left_out_of_its_document(tmp_path):
    s = settings(tmp_path)
    drive = FakeDrive([entry("a", "t1")])
    drive.image = b"\x01\x00\x00\x00 an EMF chart"
    sync.sync(s, drive)
    content = cache.read_json(s.cache_dir / "content" / "a.json")
    assert "Tekst" in content["html"] and "<img" not in content["html"]


def test_a_file_being_edited_waits_until_it_has_settled(tmp_path):
    s = settings(tmp_path)
    just_now = datetime.now(UTC).isoformat()
    drive = FakeDrive([entry("a", just_now)])
    assert sync.sync(s, drive, settle=300) is False  # nothing to show yet
    assert drive.exports == []
    assert sync.sync(s, drive, settle=0) is True  # published once settled (or on demand)
    assert drive.exports == ["a"]


def test_nothing_changed_means_no_rebuild(tmp_path):
    s = settings(tmp_path)
    drive = FakeDrive([entry("a", "2026-01-01T00:00:00Z")])
    assert sync.sync(s, drive) is True
    assert sync.sync(s, drive) is False
    drive.entries = [Entry(**{**entry("a", "2026-01-01T00:00:00Z").__dict__, "name": "renamed"})]
    assert sync.sync(s, drive) is True  # a rename changes the site without new content


def test_media_urls_do_not_reveal_drive_ids(tmp_path):
    """The folder is public: a Drive ID in a URL would lead to the original file and its folder."""
    s = settings(tmp_path)
    sync.sync(s, FakeDrive([entry("a-drive-file-id", "t1")]))
    assert [p.name for p in s.media_dir.iterdir()] == [cache.media_name("a-drive-file-id")]
    assert "a-drive-file-id" not in (s.cache_dir / "content" / "a-drive-file-id.json").read_text().split('"base"')[-1]


def test_a_file_dated_in_the_future_is_not_held_back(tmp_path):
    """A skewed clock on the uploading machine must not keep a file off the site until that date."""
    s = settings(tmp_path)
    drive = FakeDrive([entry("a", "2099-01-01T00:00:00Z")])
    sync.sync(s, drive, settle=300)
    assert drive.exports == ["a"]


def test_a_design_doc_moved_into_the_root_folder_is_read_again(tmp_path):
    """Its table is only read in the root folder; a move doesn't change the file, but must count."""
    s = settings(tmp_path)
    design = Entry(id="d", name="Design", mime=DOC, path=["drafts"], created="2026-01-01T00:00:00Z", modified="t1")
    drive = FakeDrive([design])
    sync.sync(s, drive)
    assert "design" not in cache.read_json(s.cache_dir / "content" / "d.json")

    drive.entries, drive.exports = [Entry(**{**design.__dict__, "path": []})], []
    sync.sync(s, drive)
    assert drive.exports == ["d"]
    assert "design" in cache.read_json(s.cache_dir / "content" / "d.json")

    drive.exports = []
    sync.sync(s, drive)
    assert drive.exports == []  # in place now: not read again


def test_a_file_replaced_by_a_new_upload_stays_until_the_new_one_is_out(tmp_path):
    """A new upload has a new ID and settles first; the file it replaces must not vanish meanwhile."""
    s, settled, fresh = settings(tmp_path), "2026-01-01T00:00:00Z", datetime.now(UTC).isoformat()
    old = Entry(id="old", name="Post", mime=DOC, path=["writing"], created=settled, modified=settled)
    gone = Entry(id="gone", name="Deleted", mime=DOC, path=["writing"], created=settled, modified=settled)
    drive = FakeDrive([old, gone])
    sync.sync(s, drive, settle=300)

    new = Entry(id="new", name="Post", mime=DOC, path=["writing"], created=fresh, modified=fresh)
    drive.entries = [new]
    sync.sync(s, drive, settle=300)
    shown = {e["id"] for e in cache.read_json(s.cache_dir / "manifest.json")}
    assert shown == {"new", "old", "gone"} and (s.cache_dir / "content" / "old.json").exists()

    drive.entries = [Entry(**{**new.__dict__, "modified": settled})]  # settled: it replaces the old one
    sync.sync(s, drive, settle=300)
    assert {e["id"] for e in cache.read_json(s.cache_dir / "manifest.json")} == {"new"}
    assert not (s.cache_dir / "content" / "old.json").exists()
    assert not (s.media_dir / cache.media_name("gone")).exists()


def test_an_emptied_folder_keeps_the_site_until_files_are_back(tmp_path):
    s = settings(tmp_path)
    drive = FakeDrive([entry("a", "t1")])
    sync.sync(s, drive)
    drive.entries = []  # everything deleted, about to be uploaded again
    assert sync.sync(s, drive) is False
    assert (s.cache_dir / "content" / "a.json").exists()
    assert [e["id"] for e in cache.read_json(s.cache_dir / "manifest.json")] == ["a"]


def test_loop_tries_a_failed_build_again_the_next_round(tmp_path, monkeypatch):
    from foldocs import cli

    s, drive, calls, real = settings(tmp_path), FakeDrive([entry("a", "t1")]), [], sync.sync
    monkeypatch.setattr(cli.sync, "sync", lambda settings_, settle=0: real(settings_, drive))
    monkeypatch.setattr(cli, "INTERVAL", 0)

    def build_all():
        calls.append("build")
        if len(calls) == 1:
            raise RuntimeError("a template error")

    cli.loop(s, lambda: [s], build_all, rounds=2)  # nothing changes in the folder after round one
    assert calls == ["build", "build"]
