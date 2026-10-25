"""What every source must offer, checked for every service Foldocs reads: a new one passes this
before anything else (see foldocs/sources on adding one)."""

from pathlib import Path

import pytest

from foldocs import config, sources

LINKS = {"drive": "https://drive.google.com/drive/folders/1AbC-d_E", "nextcloud": "https://cloud.example.org/s/Tok3n"}
SOURCE = (
    "kind",
    "url",
    "embed",
    "max_items",
    "max_bytes",
    "walk",
    "download",
    "file_url",
    "folder_url",
)


@pytest.mark.parametrize("provider", sources.providers(), ids=lambda p: p.kind)
def test_every_service_offers_what_a_source_must(provider, monkeypatch):
    link = LINKS[provider.kind]  # a new service adds its own sample link above
    assert provider.matches(link) and sources.provider_for(link) is provider
    provider.check(link)  # a usable link passes
    with pytest.raises(ValueError):
        provider.check("https://example.org/not/a/share")

    def no_network(*args, **kwargs):
        raise AssertionError("making a source must send no request")

    monkeypatch.setattr("requests.Session.request", no_network)
    keys = {key: "x" for key in provider.keys}
    settings = config.Settings(link, keys, Path("data"), "https://example.org", "Ann", False)
    opened = sources.open_source(settings)
    assert all(hasattr(opened, name) for name in SOURCE)
    assert opened.kind == provider.kind and opened.url.startswith("https://")
