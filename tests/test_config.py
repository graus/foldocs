"""Settings from the environment: one folder link, whichever service it is in."""

import logging

import pytest

from foldocs import config, sources
from foldocs.sources import drive

SHARE = "https://cloud.example.org/s/AbC123"

ENV = {"FOLDER_URL": SHARE, "SITE_URL": "https://example.org", "AUTHOR": "Ann"}


def test_the_folder_is_one_link_whichever_service_it_is_in(monkeypatch, caplog):
    for key, value in {**ENV, "FOLDER_PASSWORD": "pw"}.items():
        monkeypatch.setenv(key, value)
    settings = config.load()  # a Nextcloud share needs no key
    assert sources.provider_for(settings.folder_url).kind == "nextcloud" and settings.folder_password == "pw"

    link = "https://drive.google.com/drive/folders/1AbC-d_E?usp=sharing"
    monkeypatch.setenv("FOLDER_URL", link)
    with pytest.raises(SystemExit, match="GOOGLE_API_KEY"):
        config.load()  # Drive needs its key
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    with caplog.at_level(logging.WARNING, logger="foldocs.config"):
        settings = config.load()
    assert drive.folder_id(settings.folder_url) == "1AbC-d_E" and settings.folder_password == ""
    assert settings.keys == {"GOOGLE_API_KEY": "k"}
    assert "has no password" in caplog.text
    assert drive.folder_id("1AbC-d_E") == "1AbC-d_E"  # the ID alone works too


def test_the_site_url_needs_its_scheme(monkeypatch):
    for key, value in {**ENV, "SITE_URL": "example.org"}.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(SystemExit, match="SITE_URL"):
        config.load()


def test_the_default_theme_is_found_from_anywhere(monkeypatch, tmp_path):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)
    assert (config.load().theme_dir / "theme.toml").is_file()


@pytest.mark.parametrize(
    ("link", "kind"),
    [
        ("https://drive.google.com/drive/folders/1AbC-d_E?usp=sharing", "drive"),
        ("1AbC-d_E", "drive"),
        ("https://cloud.example.org/s/Tok3n", "nextcloud"),
        ("https://example.org/nextcloud/index.php/s/Tok3n", "nextcloud"),
        ("https://www.dropbox.com/scl/fo/abc/def?rlkey=x", None),  # not yet: said so, not taken for Nextcloud
        ("https://onedrive.live.com/?id=abc", None),
    ],
)
def test_each_link_is_known_by_one_service_or_refused(link, kind):
    matching = [p.kind for p in sources.providers() if p.matches(link)]
    assert matching == ([kind] if kind else [])
    if not kind:
        with pytest.raises(ValueError, match="Google Drive, Nextcloud"):
            sources.provider_for(link)
