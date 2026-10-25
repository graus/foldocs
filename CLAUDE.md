# Foldocs

Publishes a shared folder (Google Drive or Nextcloud) as a static website. README.md is the user
documentation; this file is what it doesn't say.

## Commands

```sh
uv run pytest                                      # all tests; every bundled theme is rendered too
UPDATE_CORPUS=1 uv run pytest tests/test_corpus.py  # accept changed pages of the corpus; review the diff
uv run ruff check . && uv run ruff format .        # CI fails on either
uv run foldocs check-theme [DIR]                   # tokens + WCAG AA contrast, light and dark
uv run --env-file .env foldocs sync | build | loop | serve   # .env: see .env.example
uv run foldocs serve                               # preview data/public; needs no .env
```

Before calling a change done: tests, ruff check and ruff format --check pass. CI also builds the
wheel and runs `foldocs check-theme` from it (the themes are force-included into the package).

## How it fits together

`sync` asks a source (`foldocs/sources/`: one module per service, registered in `__init__.py`)
what changed → `cache` stores the files → a document's format (`foldocs/formats/`: one module per
format, registered in `__init__.py`) reads it into clean HTML, and `convert` makes that a page the
same way for every format; `images` makes WebP variants → `site` models the folder as pages →
`render` writes them through a theme.

- Sources say where a file is, formats what it is. A file type's quirks (Docs' styled spans, flat
  lists) stay in its format's module; `convert` only takes the clean tags its docstring lists.
- A service's own document format (Google Docs) is exported through the optional `Exports`
  protocol; every source has `download`.

- Two special Doc names, in `config.py`: `intro` (opens its folder's page) and `design` (root
  only: colors and fonts, read by `design.py`). Everything else is content.
- Themes: `base` → `default`, `journal`, `index`. A theme is templates + static + `theme.toml`;
  the engine has no look or language of its own, so user-facing words go in `[strings]`.
  The engine fills `<head>` (SEO, structured data); themes don't.
- All themes share the design token names (`--color-*`, `--font-*`, `--text-*`, `--space-*`).
  A new color pair must pass `check-theme`.

## Conventions

- Python 3.13, ruff, 120 columns. Few dependencies; ask before adding one.
- Docstrings and comments are short plain sentences saying *what it is* or *why*, in the
  vocabulary of the folder ("a Doc", "a section", "the folder"), not the code. Match the
  surrounding density; don't narrate.
- Everything from a folder or a server is untrusted: parse XML only via `xmlsafe.py`, build cache
  paths only via `cache.content_path` (it rejects non-IDs), escape what goes into the feed.
- Docs and examples use the persona Jan Jansen, `example.com`, `jan@example.com`; never a real
  person. (Test fixtures use Ann and `example.org`.)
- No analytics, trackers or third-party requests in the engine or the bundled themes (the one
  exception: the default theme's demo page frames the Drive folder itself).
- Never commit `.env`, `secrets/`, `data/` or API keys.

## Gotchas

- A name starting with "." is hidden: listed, so a Markdown file can show the images in one
  (Nextcloud Text's `.attachments.<id>`), never published.
- `THEME_DIR` must start with `./` or `/`: Docker Compose treats a bare name as a named volume.
- `assets/screenshots/` and `assets/drawings/` are generated from the live foldocs.com site;
  don't edit them by hand.
