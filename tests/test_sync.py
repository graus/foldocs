"""The sync against a fake source: incremental, removing what is gone, safe on failure."""

import io
from datetime import UTC, datetime

import pytest
import requests
from PIL import Image

from foldocs import cache, config, sync
from foldocs.sources import DocExport, Entry

DOC = "application/vnd.google-apps.document"


def jpeg(color="orange") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(buf, "JPEG")
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


class FakeShare(FakeDrive):
    """A source whose documents are files, read with download()."""

    def __init__(self, entries, files):
        super().__init__(entries)
        self.files, self.downloads = files, []

    def download(self, entry):
        self.downloads.append(entry.name)
        return self.files[entry.name]


def markdown(name, path):
    return Entry(id=name.replace(".", "-"), name=name, mime="text/markdown", path=path, created="t", modified="t1")


def test_a_markdown_files_images_are_read_from_the_folder_by_their_path(tmp_path, caplog):
    s = settings(tmp_path)
    post, other = markdown("post.md", ["writing", "2026"]), markdown("other.md", ["writing"])
    photo = Entry("p", "a b.jpg", "image/jpeg", ["writing", "photos"], "t", "t1")
    files = {
        "post.md": b"Words.\n\n![](../photos/a%20b.jpg) ![](../../../outside.jpg) ![](../other.md) ![](missing.jpg)\n",
        "a b.jpg": jpeg(),
        "other.md": b"Other.",
    }
    sync.sync(s, FakeShare([post, photo, other], files))
    content = cache.read_json(s.cache_dir / "content" / "post-md.json")
    assert content["html"].count("<img") == 1 and content["cover"]  # only the photo in the folder
    assert "outside.jpg" in caplog.text and "Not an image: other.md" in caplog.text


def test_hidden_files_and_folders_are_not_published(tmp_path):
    s = settings(tmp_path)
    shown = markdown("shown.md", ["writing"])
    hidden = [markdown("draft.md", ["writing", ".drafts"]), markdown(".secret.md", ["writing"])]
    sync.sync(s, FakeShare([shown, *hidden], {"shown.md": b"Hi", "draft.md": b"x", ".secret.md": b"x"}))
    assert [e["name"] for e in cache.read_json(s.cache_dir / "manifest.json")] == ["shown.md"]
    assert cache.Cache(s.cache_dir).published() == {"shown-md"}


def test_a_page_is_redone_when_an_image_it_shows_changes(tmp_path):
    """The Markdown file stays as it is; the photo it links to is replaced, or deleted."""
    s = settings(tmp_path)
    post = markdown("post.md", ["writing"])
    photo = Entry("p", "a.jpg", "image/jpeg", ["writing", ".attachments.1"], "t", "t1")
    share = FakeShare([post, photo], {"post.md": b"![](.attachments.1/a.jpg)", "a.jpg": jpeg()})
    sync.sync(s, share)
    content = cache.read_json(s.cache_dir / "content" / "post-md.json")
    assert content["uses"] == [[["writing", ".attachments.1", "a.jpg"], ["p", "t1"]]]
    before = content["cover"]["variants"][0]["file"]

    share.downloads = []
    assert sync.sync(s, share) is False and share.downloads == []  # nothing changed: nothing read

    share.files["a.jpg"], share.entries = jpeg("navy"), [post, Entry(**{**photo.__dict__, "modified": "t2"})]
    assert sync.sync(s, share) is True
    content = cache.read_json(s.cache_dir / "content" / "post-md.json")
    assert content["uses"] == [[["writing", ".attachments.1", "a.jpg"], ["p", "t2"]]]
    assert content["cover"]["variants"][0]["file"] != before  # a new name: no browser keeps the old one

    share.entries = [post]
    assert sync.sync(s, share) is True
    assert "<img" not in cache.read_json(s.cache_dir / "content" / "post-md.json")["html"]


def test_a_page_is_redone_when_another_file_takes_its_images_place_or_one_turns_up(tmp_path):
    s, settled = settings(tmp_path), "2026-01-01T00:00:00Z"
    post = Entry("post", "post.md", "text/markdown", ["writing"], settled, settled)
    files = {"post.md": b"![](a.jpg) ![](b.jpg)", "a.jpg": jpeg(), "a2.jpg": jpeg("navy"), "b.jpg": jpeg("teal")}
    a = Entry("a", "a.jpg", "image/jpeg", ["writing"], settled, settled)
    share = FakeShare([post, a], files)
    sync.sync(s, share)
    content = cache.read_json(s.cache_dir / "content" / "post.json")
    assert content["html"].count("<img") == 1 and [["writing", "b.jpg"], None] in content["uses"]

    # a.jpg renamed away (the same file, unchanged), a new one uploaded under its name
    replaced = Entry("a2", "a.jpg", "image/jpeg", ["writing"], settled, settled)
    share.entries = [post, Entry(**{**a.__dict__, "name": "old.jpg"}), replaced]
    share.files["a.jpg"] = files["a2.jpg"]
    assert sync.sync(s, share) is True
    assert [["writing", "a.jpg"], ["a2", settled]] in cache.read_json(s.cache_dir / "content" / "post.json")["uses"]

    share.entries.append(Entry("b", "b.jpg", "image/jpeg", ["writing"], settled, settled))  # there at last
    assert sync.sync(s, share) is True
    assert cache.read_json(s.cache_dir / "content" / "post.json")["html"].count("<img") == 2


def test_a_page_waits_for_an_image_it_shows_to_settle(tmp_path):
    s, settled, fresh = settings(tmp_path), "2026-01-01T00:00:00Z", datetime.now(UTC).isoformat()
    post = Entry("post", "post.md", "text/markdown", ["writing"], settled, settled)
    photo = Entry("p", "a.jpg", "image/jpeg", ["writing"], settled, settled)
    share = FakeShare([post, photo], {"post.md": b"![](a.jpg)", "a.jpg": jpeg()})
    sync.sync(s, share, settle=300)
    share.entries, share.downloads = [post, Entry(**{**photo.__dict__, "modified": fresh})], []
    assert sync.sync(s, share, settle=300) is False and "post.md" not in share.downloads  # still being replaced
    assert sync.sync(s, share, settle=0) is True


def test_a_page_that_failed_is_tried_again_when_a_file_it_looked_for_changes(tmp_path):
    s, settled = settings(tmp_path), "2026-01-01T00:00:00Z"
    post = Entry("post", "post.md", "text/markdown", ["writing"], settled, settled)
    share = FakeShare([post], {"post.md": b"![](a.jpg)"})
    sync._failed["post"] = (settled, [[["writing", "a.jpg"], None]])  # as a failure after looking for a.jpg leaves it
    sync.sync(s, share)
    assert share.downloads == []  # nothing changed: not tried again
    share.entries.append(Entry("a", "a.jpg", "image/jpeg", ["writing"], settled, settled))
    share.files["a.jpg"] = jpeg()
    sync.sync(s, share)
    assert share.downloads[0] == "post.md"


def test_a_folder_with_a_slash_in_its_name_is_followed_too(tmp_path):
    """Drive allows "/" in a name: a path is a list of names, never one string."""
    s, settled = settings(tmp_path), "2026-01-01T00:00:00Z"
    post = Entry("post", "post.md", "text/markdown", ["2024/25"], settled, settled)
    photo = Entry("p", "a.jpg", "image/jpeg", ["2024/25"], settled, settled)
    share = FakeShare([post, photo], {"post.md": b"![](a.jpg)", "a.jpg": jpeg()})
    sync.sync(s, share)
    assert cache.read_json(s.cache_dir / "content" / "post.json")["uses"] == [[["2024/25", "a.jpg"], ["p", settled]]]
    share.entries = [post, Entry(**{**photo.__dict__, "modified": "2026-02-01T00:00:00Z"})]
    assert sync.sync(s, share) is True
