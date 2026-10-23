"""The design Doc: its table read and checked, Google Fonts self-hosted, and the CSS it becomes."""

from foldocs import design, render
from tests.test_site import doc, entry, make_site


def table(*rows):
    cells = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table>{cells}</table>"


HEADER = ("Setting", "Light", "Dark")


def test_settings_are_read_by_name_and_only_valid_values_kept():
    html = table(
        HEADER,
        ("Accent colour", "#2156C4", "8fb0ff"),  # British spelling, too
        ("Background", "#fffdf7", ""),
        ("Text", "red; } body { display: none", ""),
        ("Heading font", "https://fonts.google.com/specimen/Abril+Fatface", ""),
        ("Body font", "Lora, 'Iowan Old Style', serif", ""),
        ("Interface font", "Comic; color: red", ""),
        ("Corners", "6px", "12px"),
        ("Shadow", "big", ""),
    )
    assert design.parse(html) == {
        "accent": ["#2156c4", "#8fb0ff"],
        "background": ["#fffdf7", None],
        "heading font": [["Abril Fatface"], None],
        "body font": [["Lora", "Iowan Old Style", "serif"], None],
        "corners": ["6px", None],  # one value for both modes
    }


def test_a_table_without_a_header_keeps_its_first_setting_and_a_linked_font_its_fallback(caplog):
    html = table(("Accent", "#00875a", ""), ("Heading font", "https://fonts.google.com/specimen/Lora, serif", "Inter"))
    assert design.parse(html) == {"accent": ["#00875a", None], "heading font": [["Lora", "serif"], None]}
    assert "one value for both modes" in caplog.text  # the dark font is not silently dropped
    assert design.parse(table(HEADER)) == {} and "no setting" not in caplog.text


def test_the_stylesheet_sets_only_what_the_doc_sets():
    assert design.stylesheet({}) == ""
    css = design.stylesheet({"corners": ["4px", None], "ui font": [["Inter"], None]})
    assert "--radius: 4px;" in css and '--font-ui: "Inter", system-ui, sans-serif;' in css
    assert "--color" not in css and "@media" not in css


def test_colors_go_per_mode_and_carry_through_to_the_derived_ones():
    css = design.stylesheet({"background": ["#fffdf7", None], "accent": ["#2156c4", "#8fb0ff"]})
    light = css.split("@media (prefers-color-scheme: light)")[1].split("@media")[0]
    dark = css.split("@media (prefers-color-scheme: dark)")[1]
    assert "--color-bg: #fffdf7" in light and "--color-bg" not in dark.split("inverse")[0]  # dark keeps the theme's
    assert "--color-accent: #2156c4" in light and "--color-accent: #8fb0ff" in dark
    assert "--color-surface: var(--color-bg)" in css and "--color-accent-text: var(--color-accent)" in css


GOOGLE_CSS = b"""/* latin */
@font-face {
  font-family: 'Lora';
  font-style: normal;
  font-weight: 400 700;
  font-display: swap;
  src: url(https://fonts.gstatic.com/s/lora/v37/a.woff2) format('woff2');
  unicode-range: U+0000-00FF, U+0131;
}
@font-face {
  font-family: 'Lora';
  font-style: italic;
  font-weight: 400;
  src: url(https://evil.example/x.woff2) format('woff2');
}"""


def test_google_fonts_are_downloaded_and_served_from_the_site(tmp_path):
    asked = []

    def get(url):
        asked.append(url)
        if "fonts.gstatic.com" in url:
            return b"woff2"
        if "family=Lora:ital" in url:
            raise design.NotOnGoogle(url)  # a family without one of the styles: ask for fewer
        if "family=Lora" in url:
            return GOOGLE_CSS
        raise design.NotOnGoogle(url)

    found = {"body font": [["Lora", "Georgia", "serif"], None]}
    faces = design.fonts(found, tmp_path, "/media/abc/", get)
    assert faces.count("@font-face") == 1  # the file from elsewhere is not taken
    assert 'font-family: "Lora"' in faces and "font-weight: 400 700" in faces and "U+0000-00FF" in faces
    (font,) = tmp_path.iterdir()
    assert f"url(/media/abc/{font.name})" in faces and font.read_bytes() == b"woff2"
    assert "evil" not in " ".join(asked)
    assert any("family=Georgia" in url for url in asked)  # asked for, not found: left to the browser


def test_the_design_doc_styles_the_site_and_is_no_page(tmp_path):
    found = {"heading font": [["Abril Fatface"], None], "accent": ["#00aa55", None]}
    faces = '@font-face { font-family: "Abril Fatface"; src: url(/media/x/font-1.woff2) format("woff2"); }'
    settings = make_site(
        tmp_path,
        [
            (entry("d", "Design", []), doc("<table></table>") | {"design": found, "fonts": faces}),
            (entry("p", "Post", ["Writing"]), doc("<p>Text.</p>", title="Post")),
        ],
    )
    render.build(settings)
    public = settings.public_dir
    css = (public / "static" / "design.css").read_text()
    assert "--color-accent: #00aa55" in css and "Abril Fatface" in css
    home = (public / "index.html").read_text()
    assert home.index("style.css") < home.index("design.css")  # after the theme's own
    assert "bricolage-grotesque.woff2" not in home  # its heading font is replaced: not preloaded
    assert not (public / "design").exists() and "Design" not in home


def test_a_color_needs_its_hash_unless_it_has_six_or_eight_digits():
    from foldocs.design import _color

    assert _color("#bad") == "#bad" and _color("2156c4") == "#2156c4"
    assert _color("bad") is None and _color("fed") is None
