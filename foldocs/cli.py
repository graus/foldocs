"""Command line: foldocs sync | build | loop | serve | check-theme."""

import argparse
import functools
import http.server
import logging
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

from . import __version__, config, render, sync, themecheck
from . import theme as themes
from .site import NothingSynced

log = logging.getLogger("foldocs")

INTERVAL = 60  # seconds between checks of the folder in `loop`
SETTLE = 5 * 60  # in `loop`, a file is published once untouched this long (editors save as you type)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="foldocs", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("sync", help="fetch all changes from the folder now, then build the site")
    commands.add_parser("build", help="build the site from the local cache only")
    commands.add_parser(
        "loop", help=f"check the folder every {INTERVAL} s; publish a file once untouched for {SETTLE // 60} minutes"
    )
    serve = commands.add_parser("serve", help="preview the built site (needs no folder settings)")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--dir", type=Path, help="the site (default: DATA_DIR/public)")
    check = commands.add_parser("check-theme", help="check a theme's design tokens and WCAG AA contrast")
    check.add_argument("folder", nargs="?", help="the theme (default: THEME_DIR)")
    args = parser.parse_args(argv)

    if args.command == "check-theme":  # needs no folder settings
        folder = Path(args.folder or os.environ.get("THEME_DIR") or themes.DEFAULT)
        try:
            problems = themecheck.check(themes.load(folder))
        except themes.ThemeError as e:
            raise SystemExit(str(e)) from None
        for problem in problems:
            print(problem)
        print(f"{folder}: {'passes' if not problems else f'{len(problems)} problems'}")
        sys.exit(1 if problems else 0)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.command == "serve":
        folder = args.dir or Path(os.environ.get("DATA_DIR") or "data") / "public"
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=folder)
        log.info("Serving %s at http://localhost:%d", folder, args.port)
        http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler).serve_forever()
        return

    settings = config.load()

    def builds() -> list[config.Settings]:
        """The site, then each variant of it (a variant added to a mounted theme counts at once)."""
        return [settings, *config.variants(settings)]

    def build_all() -> None:
        for each in builds():
            render.build(each)

    try:
        if args.command == "sync":
            sync.sync(settings)
            build_all()
        elif args.command == "build":
            build_all()
    except (themes.ThemeError, NothingSynced) as e:
        raise SystemExit(str(e)) from None
    if args.command == "loop":
        loop(settings, builds, build_all)


def loop(
    settings: config.Settings,
    builds: Callable[[], list[config.Settings]],
    build_all: Callable[[], None],
    rounds: int | None = None,
) -> None:
    """Sync and build every INTERVAL seconds. The first round publishes everything at once, so a
    restart is a "publish now". Later rounds rebuild when the content changed, or a theme did (a
    mounted theme can change); a build that failed is tried again the next round."""
    settle, built_with, pending = 0, None, False
    while rounds is None or rounds > 0:
        try:
            pending |= sync.sync(settings, settle=settle)
            fingerprint = "".join(themes.load(each.theme_dir).fingerprint() for each in builds())
            if pending or fingerprint != built_with:
                build_all()
                pending, built_with = False, fingerprint
            settle = SETTLE
        except Exception:
            log.exception("Sync or build failed; the site stays as it was until the next attempt")
        if rounds is not None:
            rounds -= 1
            if not rounds:
                break
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
