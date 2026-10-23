"""Themes: the shared design system (see foldocs/themecheck.py), and themes that extend themes."""

import pytest

from foldocs import theme as themes
from foldocs import themecheck

THEMES = themes.bundled()


@pytest.mark.parametrize("theme_dir", THEMES, ids=lambda p: p.name)
def test_every_bundled_theme_has_the_tokens_and_meets_wcag_aa(theme_dir):
    assert themecheck.check(themes.load(theme_dir)) == []


def test_the_check_reports_weak_contrast_and_missing_tokens(tmp_path):
    child = tmp_path / "child"
    (child / "static").mkdir(parents=True)
    (child / "theme.toml").write_text('extends = "default"\nstylesheets = ["weak.css"]\n')
    (child / "static" / "weak.css").write_text(":root { --color-text-muted: #bbbbbb; }")
    problems = themecheck.check(themes.load(child))
    assert any("--color-text-muted on --color-bg" in p for p in problems)
    assert themecheck.contrast("#000000", "#ffffff") == 21


def test_a_theme_can_extend_another_and_change_only_what_differs(tmp_path):
    child = tmp_path / "child"
    (child / "templates").mkdir(parents=True)
    (child / "static").mkdir()
    (child / "theme.toml").write_text(
        'extends = "default"\nlanguage = "nl"\nstylesheets = ["extra.css"]\n'
        '[strings]\nall = "Alles"\n[extras.demo]\nlabel = "De map"\n'
    )
    (child / "templates" / "404.html").write_text("eigen")
    (child / "static" / "extra.css").write_text(":root { --color-accent: #000000; }")
    theme = themes.load(child)
    assert [d.name for d in theme.dirs[1:]] == ["default", "base"]
    assert theme.strings["all"] == "Alles" and theme.strings["see_all"] == "See all →"  # the rest inherited
    assert theme.extras["demo"].label == "De map" and theme.extras["demo"].url == "/demo/"  # merged per key
    assert theme.stylesheets == ("base.css", "style.css", "extra.css")  # the furthest ancestor's first
    assert [p.parent.name for p in theme.templates] == ["child", "default", "base"]
    assert {"base.css", "style.css", "extra.css"} <= set(theme.static_files())


def test_a_changed_theme_file_changes_the_fingerprint(tmp_path):
    child = tmp_path / "child"
    child.mkdir()
    (child / "theme.toml").write_text('extends = "default"\nstylesheets = ["extra.css"]\n')
    (child / "extra.css").write_text("a {}")
    before = themes.load(child).fingerprint()
    assert themes.load(child).fingerprint() == before
    (child / "extra.css").write_text("a { color: red; }")
    assert themes.load(child).fingerprint() != before


def test_a_light_only_override_breaks_dark_mode_and_the_check_says_so(tmp_path):
    """A plain :root in a later stylesheet wins in dark mode too, over the parent's dark values."""
    child = tmp_path / "child"
    (child / "static").mkdir(parents=True)
    (child / "theme.toml").write_text('extends = "default"\nstylesheets = ["light.css"]\n')
    (child / "static" / "light.css").write_text(":root { --color-bg: #ffffff; }")
    problems = themecheck.check(themes.load(child))
    assert any(p.startswith("dark: --color-text on --color-bg") for p in problems)


def test_an_empty_template_switches_off_an_inherited_extra_page(tmp_path):
    child = tmp_path / "child"
    child.mkdir()
    (child / "theme.toml").write_text('extends = "default"\n[extras.demo]\ntemplate = ""\n')
    theme = themes.load(child)
    assert "demo" not in theme.extras and "concept" in theme.extras


def test_a_color_set_to_another_token_is_followed_and_anything_else_reported(tmp_path):
    child = tmp_path / "child"
    (child / "static").mkdir(parents=True)
    (child / "theme.toml").write_text('extends = "default"\nstylesheets = ["extra.css"]\n')
    (child / "static" / "extra.css").write_text(":root { --color-surface: var(--color-bg); --color-band: rgb(1 2 3); }")
    problems = themecheck.check(themes.load(child))
    assert problems == [
        "light: --color-band is rgb(1 2 3), not a hex color this check can read",
        "dark: --color-band is rgb(1 2 3), not a hex color this check can read",
    ]


def test_light_colors_from_the_design_document_count():
    css = ":root { --color-bg: #fff; } @media (prefers-color-scheme: light) { :root { --color-bg: #f6f1e7; } }"
    light, dark = themecheck.tokens_in(css)
    assert (light["color-bg"], dark["color-bg"]) == ("#f6f1e7", "#fff")


@pytest.mark.parametrize("toml", ['extends = "default"\n[strings', 'language = "en"'])
def test_a_broken_theme_toml_is_a_theme_error(tmp_path, toml):
    (tmp_path / "theme.toml").write_text(toml)
    with pytest.raises(themes.ThemeError):
        themes.load(tmp_path)


def test_hidden_files_are_no_part_of_a_theme(tmp_path):
    (tmp_path / "theme.toml").write_text('extends = "default"\n')
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / ".DS_Store").write_bytes(b"x")
    assert ".DS_Store" not in themes.load(tmp_path).static_files()
