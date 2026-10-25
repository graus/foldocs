"""From cache to site: pages, sections and their order, URLs, and a full render with the theme."""

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from foldocs import config, contact, render, site, theme
from foldocs.formats import DOC, DOCX

THEME = theme.load(Path(__file__).parent.parent / "themes" / "default")


def entry(id, name, path, kind="doc", created="2026-10-01T10:00:00Z"):
    return {
        "id": id,
        "name": name,
        "path": path,
        "created": created,
        "modified": created,
        "mime": {"doc": DOC, "docx": DOCX}.get(kind, "image/jpeg"),
        "description": "",
        "taken": None,
    }


def _first(html):
    """What convert.convert records of a document's first paragraph, heading and list."""
    from bs4 import BeautifulSoup

    from foldocs.convert import _first

    return _first(list(BeautifulSoup(html, "html.parser").find_all(recursive=False)))


def doc(html, title=None, subtitle=None, text=""):
    return {
        "modified": "",
        "version": 0,
        "title": title,
        "subtitle": subtitle,
        "html": html,
        "text": text,
        "cover": None,
        "links": re.findall(r'href="([^"]+)"', html),
        "first": _first(html),
    }


IMAGE = {
    "base": "/media/m/",
    "variants": [{"file": "me-480.webp", "w": 480}],
    "width": 480,
    "height": 600,
    "color": "#888",
}


def make_site(tmp_path, files):
    settings = config.Settings(
        "root", {"GOOGLE_API_KEY": "api-key"}, tmp_path, "https://example.org", "Ann Smith", False
    )
    content = tmp_path / "cache" / "content"
    content.mkdir(parents=True)
    (tmp_path / "cache" / "manifest.json").write_text(json.dumps([e for e, _ in files]))
    for e, c in files:
        (content / f"{e['id']}.json").write_text(json.dumps(c))
    return settings


def test_docs_in_the_root_are_pages_in_the_menu_by_their_names(tmp_path):
    settings = make_site(
        tmp_path,
        [
            (entry("c", "Contact", []), doc("<p>Write.</p>", title="Get in touch")),
            (entry("a", "02 Over mij", []), doc("<p>Bio.</p>")),
            (entry("b", "CV", []), doc("<p>Work.</p>")),
            (entry("e", "01 Work with me", []), doc("<p>Hire.</p>")),
            (entry("d", "design", []), doc("<table></table>") | {"design": {}, "fonts": ""}),
        ],
    )
    s = site.collect(settings, THEME)
    # Numbered first, in number order, then by name; the address and label from the name, not the title
    assert [(p.label, p.url) for p in s.pages] == [
        ("Work with me", "/work-with-me/"), ("Over mij", "/over-mij/"), ("Contact", "/contact/"), ("CV", "/cv/")
    ]  # fmt: skip
    assert s.pages[2].heading == "Get in touch" and s.pages[2].folders == []  # Home › Get in touch


def test_the_root_intro_opens_the_home_page_and_holds_the_contact_links(tmp_path):
    intro = (
        "<p>Intro.</p><h2>Topics</h2><ul><li>One</li></ul>"
        '<p><a href="mailto:ann@example.org?subject=Hi">mail</a> · <a href="https://www.linkedin.com/in/ann">in</a> · '
        '<a href="https://mastodon.social/@ann">toot</a> · <a href="https://example.org/blog">blog</a></p>'
    )
    settings = make_site(tmp_path, [(entry("d", "intro", []), doc(intro, "Big idea", "Strategist"))])
    s = site.collect(settings, THEME)
    assert (s.hero.headline, s.hero.label, s.hero.intro) == ("Big idea", "Strategist", "Intro.")
    assert (s.hero.list_title, s.hero.items) == ("Topics", ["One"])
    assert s.email == "ann@example.org"
    assert s.profiles == [("LinkedIn", "https://www.linkedin.com/in/ann"), ("Mastodon", "https://mastodon.social/@ann")]


def test_without_an_intro_the_home_page_opens_with_the_authors_name(tmp_path):
    settings = make_site(tmp_path, [(entry("a", "About", []), doc("<h2>Strategist</h2>", text="Bio."))])
    s = site.collect(settings, THEME)
    assert (s.hero.headline, s.hero.label, s.hero.intro, s.email) == ("Ann Smith", None, None, None)


def test_files_in_the_root_are_the_sites_own_and_every_folder_is_a_section(tmp_path):
    settings = make_site(
        tmp_path,
        [
            (entry("h", "INTRO", []), doc("<p>Intro.</p>", title="Big idea")),  # its name ignores case
            (entry("p", "A page", ["site"]), doc("<p>Hi.</p>")),
            (entry("m", "me.jpg", [], kind="image"), {"image": {"src": "/media/m.jpg"}}),
            (entry("o", "old.jpg", [], "image", "2026-01-01T10:00:00Z"), {"image": {"src": "/media/o.jpg"}}),
        ],
    )
    s = site.collect(settings, THEME)
    assert s.hero.headline == "Big idea" and s.portrait == {"src": "/media/m.jpg"}  # the newest photo
    assert [c.url for c in s.sections] == ["/site/"]  # no folder is special


def test_top_level_folders_are_sections_in_the_order_of_their_numbers(tmp_path):
    settings = make_site(
        tmp_path,
        [
            (entry("p", "Thought", ["02 Personal", "writing"]), doc("<p>Hi.</p>")),
            (entry("w", "Big job", ["01 Work"]), doc("<p>Work.</p>")),
        ],
    )
    s = site.collect(settings, THEME)
    assert [(c.name, c.url) for c in s.sections] == [("Work", "/work/"), ("Personal", "/personal/")]
    personal = s.sections[1]
    assert [c.url for c in personal.children] == ["/personal/writing/"]
    assert personal.items[0].url == "/personal/writing/thought/"
    assert personal.items[0].folders == [("Personal", "/personal/"), ("writing", "/personal/writing/")]
    assert len(s.items) == 2


def test_an_intro_doc_names_describes_and_opens_its_folder(tmp_path):
    settings = make_site(
        tmp_path,
        [
            (entry("w", "01 Big job", ["01 Work"]), doc("<p>Work.</p>")),
            (entry("v", "02 Small job", ["01 Work"]), doc("<p>Less work.</p>")),
            (
                entry("i", "Intro", ["01 Work"]),
                doc("<p>Ten years of it.</p>", "What I do", "For clients.") | {"cover": {"src": "/m.jpg"}},
            ),
            (entry("t", "Trip", ["01 Work", "travel"]), doc("<p>Away.</p>")),
            (entry("j", "INTRO.docx", ["01 Work", "travel"], kind="docx"), doc("<p>Op reis.</p>")),
            (entry("h", "intro", []), doc("<p>Hi.</p>", title="Big idea")),
        ],
    )
    s = site.collect(settings, THEME)
    work = s.sections[0]
    assert (work.name, work.url, work.description) == ("What I do", "/work/", "For clients.")  # the address stays
    assert work.intro == "<p>Ten years of it.</p>" and work.image == {"src": "/m.jpg"}  # its picture
    assert [i.title for i in work.items] == ["Big job", "Small job", "Trip"] and not work.ordered  # no card
    travel = work.children[0]
    assert (travel.name, travel.description, travel.intro) == ("travel", "", "<p>Op reis.</p>")
    assert work.items[2].folders == [("What I do", "/work/"), ("travel", "/work/travel/")]
    assert s.hero.headline == "Big idea" and s.pages == []  # the root folder's intro opens the home page


def test_a_folder_of_numbered_files_keeps_their_order(tmp_path):
    settings = make_site(
        tmp_path,
        [
            (entry("b", "02 Second", ["Work"], created="2026-01-01T10:00:00Z"), doc("<p>.</p>")),
            (entry("a", "01 First", ["Work"], created="2026-02-01T10:00:00Z"), doc("<p>.</p>")),
            (entry("x", "Older", ["Notes"], created="2026-01-01T10:00:00Z"), doc("<p>.</p>")),
            (entry("y", "Newer", ["Notes"], created="2026-02-01T10:00:00Z"), doc("<p>.</p>")),
        ],
    )
    notes, work = site.collect(settings, THEME).sections  # unnumbered sections go alphabetically
    assert work.ordered and [i.title for i in work.items] == ["First", "Second"]
    assert not notes.ordered and [i.title for i in notes.items] == ["Newer", "Older"]
    assert work.items[0].older is work.items[1]  # neighbors follow the section's order


def test_a_date_in_the_file_name_backdates_a_post(tmp_path):
    settings = make_site(tmp_path, [(entry("p", "2026-08-26 Support the comeback", ["writing"]), doc("<p>Hi.</p>"))])
    item = site.collect(settings, THEME).items[0]
    assert item.title == "Support the comeback"
    assert item.date.date().isoformat() == "2026-08-26"
    assert item.url == "/writing/support-the-comeback/"


def test_the_whole_site_renders_with_the_theme(tmp_path):
    settings = make_site(
        tmp_path,
        [
            (entry("a", "About", []), doc("<p>Bio.</p>")),
            (
                entry("i", "intro", []),
                doc(
                    '<p>Hi.</p><p><a href="mailto:ann@example.org">mail</a> <a href="https://bsky.app/profile/ann">b</a></p>'
                ),
            ),
            (entry("c", "Contact", []), doc("<p>Write.</p>")),
            (entry("w", "01 Big job", ["01 Work"]), doc("<p>Work.</p>")),
            (entry("s", "2026-08-26 Thought", ["02 Personal", "opinion"]), doc("<p>Hi.</p>")),
        ],
    )
    render.build(replace(settings, theme_dir=THEME.dir))
    public = settings.public_dir
    for page in [
        "index.html",
        "about/index.html",
        "contact/index.html",
        "work/index.html",
        "work/big-job/index.html",
        "personal/index.html",
        "personal/opinion/thought/index.html",
        "how-it-works/index.html",
        "404.html",
        "feed.xml",
    ]:
        assert (public / page).is_file(), page
    home = (public / "index.html").read_text()
    assert "ann@example.org" in home and 'class="row row--dark"' in home
    menu = home[home.index('class="site-nav"') : home.index("</nav>")]  # sections, a rule, then the pages
    assert menu.index("/personal/") < menu.index("site-nav-rule") < menu.index('href="/about/" class="site-nav-page"')
    group = menu[menu.index('class="site-nav-group"') :]  # a section's subfolders, behind a button beside it
    assert '<a href="/personal/opinion/">Opinion</a>' in group and "<summary>" in group
    footer = home[home.index("<footer") :]  # the profile as its logo, named for screen readers
    assert '<svg class="icon"' in footer and '<span class="visually-hidden">Bluesky</span>' in footer
    for page in public.rglob("*.html"):  # no Python leaking into a page
        assert not any(leak in page.read_text() for leak in ("built-in method", "<function", "None</")), page


def test_a_site_without_optional_pages_renders_too(tmp_path):
    settings = make_site(tmp_path, [(entry("s", "Thought", ["notes"]), doc("<p>Hi.</p>"))])
    render.build(replace(settings, theme_dir=THEME.dir))
    assert (settings.public_dir / "notes" / "thought" / "index.html").is_file()


@pytest.mark.parametrize("theme_dir", theme.bundled(), ids=lambda p: p.name)
def test_every_bundled_theme_renders(tmp_path, theme_dir):
    settings = make_site(
        tmp_path,
        [
            (entry("a", "About", []), doc("<p>Bio.</p>")),
            (
                entry("i", "intro", []),
                doc('<p>Hi.</p><p><a href="mailto:a@b.org">m</a> <a href="https://github.com/ann">g</a></p>'),
            ),
            (entry("m", "me.jpg", [], "image"), {"image": IMAGE}),
            (entry("w", "01 Big job", ["01 Work"]), doc("<p>Work.</p>")),
            (
                entry("v", "intro", ["01 Work"]),
                doc('<p>See <a href="https://example.org/work/big-job/">it</a>.</p>', "Jobs", "All of them."),
            ),
            (entry("s", "2026-08-26 Thought", ["02 Personal", "writing"]), doc("<p>Hi.</p>")),
        ],
    )
    render.build(replace(settings, theme_dir=theme_dir))
    work = (settings.public_dir / "work" / "index.html").read_text()
    assert "All of them." in work and 'href="/work/big-job/"' in work  # the intro, its link made local
    assert "https://github.com/ann" in (settings.public_dir / "index.html").read_text()
    for page in [
        "index.html",
        "work/index.html",
        "personal/writing/thought/index.html",
        "about/index.html",
        "404.html",
    ]:
        assert (settings.public_dir / page).is_file(), page


def test_every_page_tells_search_engines_what_it_is(tmp_path):
    settings = make_site(
        tmp_path,
        [
            (
                entry("i", "intro", []),
                doc('<p>Bio.</p><p><a href="https://github.com/ann">gh</a></p>', None, "Strategist"),
            ),
            (entry("a", "About", []), doc("<p>Bio.</p>")),
            (entry("s", "2026-08-26 Thought", ["02 Personal", "writing"]), doc("<p>Hi.</p>", subtitle="A thought.")),
        ],
    )
    render.build(replace(settings, theme_dir=THEME.dir))
    public = settings.public_dir

    def graph(page: str) -> dict:
        html = (public / page).read_text()
        data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)[1])
        return {node["@type"]: node for node in data["@graph"]}

    post = graph("personal/writing/thought/index.html")
    assert post["BlogPosting"]["headline"] == "Thought"
    assert post["BlogPosting"]["datePublished"].startswith("2026-08-26")
    assert post["BlogPosting"]["author"] == {"@id": "https://example.org/#person"}
    assert post["Person"]["jobTitle"] == "Strategist"
    assert [step["name"] for step in post["BreadcrumbList"]["itemListElement"]] == [
        "Home", "Personal", "writing", "Thought"
    ]  # fmt: skip
    assert post["Person"]["sameAs"] == ["https://github.com/ann"]
    assert "WebPage" in graph("about/index.html")
    assert "CollectionPage" in graph("personal/index.html")

    html = (public / "personal/writing/thought/index.html").read_text()
    assert '<meta name="description" content="A thought.">' in html
    assert '<link rel="canonical" href="https://example.org/personal/writing/thought/">' in html
    assert "<lastmod>2026-08-26" in (public / "sitemap.xml").read_text()
    assert 'name="robots" content="noindex' in (public / "404.html").read_text()


def test_a_variant_is_the_same_site_in_another_theme_linking_back(tmp_path):
    settings = make_site(tmp_path, [(entry("s", "Thought", ["notes"]), doc("<p>Hi.</p>"))])
    themes = tmp_path / "theme"
    (themes / "variants" / "plain").mkdir(parents=True)
    (themes / "theme.toml").write_text('extends = "default"\n')
    (themes / "variants" / "plain" / "theme.toml").write_text(
        'extends = "journal"\n[strings]\nnotice = "Another look."\n'
    )
    (themes / "variants" / "My theme").mkdir()
    (themes / "variants" / "My theme" / "theme.toml").write_text('extends = "journal"\n')
    settings = replace(settings, theme_dir=themes)
    (variant,) = config.variants(settings)  # every theme in the theme's variants/ folder named as a host can be
    assert variant.site_url == "https://plain.example.org" and variant.public_dir == tmp_path / "variants" / "plain"
    assert config.variants(variant) == []  # a variant has none of its own
    render.build(variant)
    home = (variant.public_dir / "index.html").read_text()
    assert "Another look." in home and '<a href="https://example.org/">See the original design</a>' in home
    assert '<link rel="canonical" href="https://plain.example.org/">' in home
    assert 'name="robots" content="noindex' in home  # a copy to look at, not to find
    robots = (variant.public_dir / "robots.txt").read_text()  # crawlers may read it, to see the noindex
    assert "Allow: /" in robots and "Sitemap" not in robots


def test_a_variant_without_a_notice_still_links_back(tmp_path):
    settings = make_site(tmp_path, [(entry("s", "Thought", ["notes"]), doc("<p>Hi.</p>"))])
    themes = tmp_path / "theme"
    (themes / "variants" / "plain").mkdir(parents=True)
    (themes / "theme.toml").write_text('extends = "default"\n')
    (themes / "variants" / "plain" / "theme.toml").write_text('extends = "journal"\n')
    (variant,) = config.variants(replace(settings, theme_dir=themes))
    render.build(variant)
    assert "See the original design</a>" in (variant.public_dir / "index.html").read_text()


def test_the_notice_is_a_word_of_the_theme(tmp_path):
    settings = make_site(tmp_path, [(entry("s", "Thought", ["notes"]), doc("<p>Hi.</p>"))])
    render.build(replace(settings, theme_dir=THEME.dir))
    assert 'class="notice"' not in (settings.public_dir / "index.html").read_text()  # none by default
    theme = tmp_path / "theme"
    theme.mkdir()
    (theme / "theme.toml").write_text('extends = "default"\n[strings]\nnotice = "A proposal."\n')
    render.build(replace(settings, theme_dir=theme))
    assert '<aside class="notice">A proposal.' in (settings.public_dir / "index.html").read_text()


def test_a_theme_can_link_each_page_to_its_source_in_drive(tmp_path):
    settings = make_site(tmp_path, [(entry("doc1", "Thought", ["notes"]), doc("<p>Hi.</p>"))])
    render.build(replace(settings, theme_dir=THEME.dir))
    page = settings.public_dir / "notes" / "thought" / "index.html"
    assert "docs.google.com" not in page.read_text()  # not unless the theme asks
    theme = tmp_path / "theme"
    theme.mkdir()
    (theme / "theme.toml").write_text('extends = "default"\n[strings]\nsource_link = "Open in Google Docs"\n')
    render.build(replace(settings, theme_dir=theme))
    assert 'href="https://docs.google.com/document/d/doc1/edit"' in page.read_text()


def test_a_theme_with_a_logo_shows_it_in_place_of_the_name(tmp_path):
    settings = make_site(tmp_path, [(entry("s", "Thought", ["notes"]), doc("<p>Hi.</p>"))])
    theme = tmp_path / "theme"
    (theme / "static").mkdir(parents=True)
    (theme / "theme.toml").write_text('extends = "default"\n')
    (theme / "static" / "logo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    render.build(replace(settings, theme_dir=theme))
    header = (settings.public_dir / "index.html").read_text().split("</header>")[0]
    assert '<img src="/static/logo.svg?v=' in header and 'alt="Ann Smith"' in header


def test_a_theme_can_add_to_every_pages_head(tmp_path):
    settings = make_site(tmp_path, [(entry("s", "Thought", ["notes"]), doc("<p>Hi.</p>"))])
    theme = tmp_path / "theme"
    (theme / "templates").mkdir(parents=True)
    (theme / "theme.toml").write_text('extends = "default"\n')
    (theme / "templates" / "_head.html").write_text('<script defer src="https://stats.example/s.js"></script>')
    render.build(replace(settings, theme_dir=theme))
    for page in ("index.html", "notes/thought/index.html", "404.html"):
        head = (settings.public_dir / page).read_text().split("</head>")[0]
        assert 'src="https://stats.example/s.js"' in head, page


def test_links_to_the_site_itself_become_local():
    html = (
        '<a href="https://example.org/guide/start/">a</a> <a href="https://www.example.org">b</a> '
        '<a href="http://example.org#top">c</a> <a href="https://example.org.evil.com/x">d</a>'
    )
    assert render.local_links(html, "https://example.org") == (
        '<a href="/guide/start/">a</a> <a href="/">b</a> <a href="/#top">c</a> '
        '<a href="https://example.org.evil.com/x">d</a>'
    )


def test_the_feed_makes_local_urls_absolute_and_leaves_text_alone(tmp_path):
    settings = make_site(
        tmp_path, [(entry("s", "Thought", ["notes"]), doc('<p>See <a href="/notes/">notes</a> and/or /etc.</p>'))]
    )
    render.build(replace(settings, theme_dir=THEME.dir))
    feed = (settings.public_dir / "feed.xml").read_text()
    assert "href=&#34;https://example.org/notes/&#34;" in feed or 'href="https://example.org/notes/"' in feed
    assert "and/or /etc." in feed


def test_a_link_to_a_page_the_site_lacks_stays_full():
    html = '<a href="https://example.org/demo/">a</a> <a href="https://example.org/notes/#x">b</a>'
    assert render.local_links(html, "https://example.org", {"/", "/notes/"}) == (
        '<a href="https://example.org/demo/">a</a> <a href="/notes/#x">b</a>'
    )


def test_a_themes_share_image_previews_pages_without_an_image(tmp_path):
    from PIL import Image

    settings = make_site(tmp_path, [(entry("s", "Thought", ["notes"]), doc("<p>Hi.</p>"))])
    theme = tmp_path / "theme"
    (theme / "static").mkdir(parents=True)
    (theme / "theme.toml").write_text('extends = "default"\n')
    Image.new("RGB", (1200, 630), "white").save(theme / "static" / "share.png")
    render.build(replace(settings, theme_dir=theme))
    for page in ("index.html", "notes/index.html", "notes/thought/index.html"):
        html = (settings.public_dir / page).read_text()
        assert 'property="og:image" content="https://example.org/static/share.png?v=' in html, page
        assert 'property="og:image:width" content="1200"' in html, page


def test_a_title_can_t_break_out_of_the_structured_data(tmp_path):
    """Titles and names go into a <script> in the head: no <, > or & in it can end it or open a tag."""
    title = "Ann <body> </script><!--<script> & co"
    settings = make_site(tmp_path, [(entry("s", "Post", ["Writing"]), doc("<p>Hi.</p>", title=title))])
    render.build(replace(settings, theme_dir=THEME.dir, author=title))
    (page,) = [p for p in (settings.public_dir / "writing").glob("*/index.html")]  # its address is its title's
    html = page.read_text()
    ld = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)[1]
    assert not re.search(r"[<>&]", ld)
    assert any(node.get("headline") == title for node in json.loads(ld)["@graph"])


def test_a_home_row_counts_its_pages_only_when_it_shows_some_of_them(tmp_path):
    """No row index and no numbers on cards: "See all 5 →" says there's more, the subfolder says where."""
    files = [(entry(f"g{n}", f"0{n} Page {n}", ["1 Guide", "Basics"]), doc(f"<p>Page {n}.</p>")) for n in range(1, 6)]
    files += [(entry("w", "Short", ["2 Writing"]), doc("<p>Hi.</p>", title="Short"))]
    settings = make_site(tmp_path, files)
    render.build(replace(settings, theme_dir=THEME.dir))
    home = (settings.public_dir / "index.html").read_text()
    guide, writing = home.split('id="row-2"')
    assert "See all 5 →" in guide and "See all →" in writing and "See all 1" not in writing
    assert "row-index" not in home and not re.search(r'class="meta">\s*0\d', home)
    assert re.search(r'class="meta">\s*basics\s*</p>', guide)  # the subfolder, not "01 · basics"


def test_a_page_never_takes_a_sections_address(tmp_path):
    settings = make_site(
        tmp_path,
        [
            (entry("p", "Work", [], created="2026-01-01T10:00:00Z"), doc("<p>Older than the folder.</p>")),
            (entry("w", "Job", ["1 Work"], created="2026-02-01T10:00:00Z"), doc("<p>.</p>")),
        ],
    )
    s = site.collect(settings, THEME)
    assert (s.sections[0].url, s.pages[0].url) == ("/work/", "/work-2/")


def test_a_folder_with_only_an_intro_is_no_section_yet(tmp_path):
    settings = make_site(tmp_path, [(entry("i", "intro", ["Talks"]), doc("<p>Soon.</p>", "Talks"))])
    assert site.collect(settings, THEME).sections == []


@pytest.mark.parametrize(
    ("href", "profile"),
    [
        ("https://www.linkedin.com/in/ann/", "LinkedIn"),
        ("https://nl.linkedin.com/in/ann", "LinkedIn"),
        ("https://bsky.app/profile/ann.bsky.social", "Bluesky"),
        ("https://x.com/ann", "X"),
        ("https://github.com/ann", "GitHub"),
        ("https://www.youtube.com/@ann", "YouTube"),
        ("https://ann.substack.com", "Substack"),
        ("https://scholar.google.com/citations?user=abc", "Google Scholar"),
        ("https://mastodon.social/@ann", "Mastodon"),
        ("https://github.com/ann/project", "GitHub"),  # a project's home is its profile
        ("https://github.com/ann/project/issues/1", None),
        ("https://www.youtube.com/watch?v=1", None),
        ("https://x.com/intent/post", None),
        ("https://blog.example/@ann/a-post", None),
        ("http://mastodon.social/@ann", None),
        ("https://example.org/about", None),
        ("https://medium.com", None),  # the site itself
        ("https://substack.com/", None),
    ],
)
def test_links_to_profiles_are_recognized_by_their_address(href, profile):
    assert (contact.profile(href) or (None,))[0] == profile


def test_each_profile_is_listed_once(tmp_path):
    hrefs = ["https://linkedin.com/in/ann", "https://www.linkedin.com/in/ann/"]
    assert contact.links(hrefs) == (None, [("LinkedIn", "https://linkedin.com/in/ann")])
    scholars = ["https://scholar.google.com/citations?user=a", "https://scholar.google.com/citations?user=b"]
    assert len(contact.links(scholars)[1]) == 2


def test_a_file_a_stylesheet_names_is_versioned_like_the_stylesheet(tmp_path):
    """Static files are cached for a year: the font's URL must change when the font does."""
    settings = make_site(tmp_path, [(entry("s", "Thought", ["notes"]), doc("<p>Hi.</p>"))])
    render.build(replace(settings, theme_dir=THEME.dir))
    css = (settings.public_dir / "static" / "style.css").read_text()
    home = (settings.public_dir / "index.html").read_text()
    version = re.search(r"url\(fonts/bricolage-grotesque\.woff2\?v=(\w+)\)", css)[1]
    assert f'href="/static/fonts/bricolage-grotesque.woff2?v={version}" as="font"' in home  # the preload, the same URL


def test_a_logo_takes_its_space_before_it_loads():
    assert render._svg_size(b'<svg xmlns="http://www.w3.org/2000/svg" width="558" height="114">') == (558, 114)
    assert render._svg_size(b'<svg viewBox="0 0 24 12.5">') == (24, 12)
    assert render._svg_size(b"<svg>") is None


def test_a_photo_in_the_feed_is_escaped_once(tmp_path):
    photo = entry("p", "Studio.jpg", ["notes"], "image") | {"description": "Ink & paper"}
    settings = make_site(tmp_path, [(photo, {"image": IMAGE})])
    render.build(replace(settings, theme_dir=THEME.dir))
    feed = (settings.public_dir / "feed.xml").read_text()
    assert "&lt;p&gt;Ink &amp;amp; paper&lt;/p&gt;" in feed  # <p>Ink &amp; paper</p>, as text
    assert "&amp;lt;" not in feed


def test_links_to_other_files_in_the_folder_lead_to_their_pages():
    """By the ID (the file may have been renamed since), else by the path (added since); a folder's
    path ends in "", so a file of the same name can't take its place."""
    destinations = {
        "a1": "/notes/plan/",
        ("notes", "New.md"): "/notes/new/",
        ("notes", "Trip"): "/notes/trip-doc/",  # a Doc named Trip, beside the folder Trip
        ("notes", "Trip", ""): "/notes/trip/",
        ("",): "/",
        "f9": "/notes/trip/",
    }
    google = "folder:?id={}&amp;url=https%3A%2F%2Fdocs.google.com%2Fdocument%2Fd%2F{}%2Fedit"
    html = (
        '<p><a href="folder:/notes/Old%20name.md?id=a1#day">renamed</a> <a href="folder:/notes/New.md">new</a> '
        '<a href="folder:/notes/Trip/">folder</a> <a href="folder:/notes/Trip">doc</a> <a href="folder:/">home</a> '
        '<a href="folder:/gone.md?id=zz">gone</a> <a href="folder:/gone.md" id="ref-1">marked</a> '
        f'<a href="{google.format("a1", "a1")}">doc by ID</a> <a href="{google.format("f9", "f9")}">folder by ID</a> '
        f'<a href="{google.format("xx", "xx")}">elsewhere</a></p>'
    )
    assert render.folder_links(html, destinations) == (
        '<p><a href="/notes/plan/#day">renamed</a> <a href="/notes/new/">new</a> '
        '<a href="/notes/trip/">folder</a> <a href="/notes/trip-doc/">doc</a> <a href="/">home</a> '
        'gone <a id="ref-1">marked</a> <a href="/notes/plan/">doc by ID</a> <a href="/notes/trip/">folder by ID</a> '
        '<a href="https://docs.google.com/document/d/xx/edit">elsewhere</a></p>'
    )
