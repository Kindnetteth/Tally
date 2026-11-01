"""Command line: `python -m tally <command>`.

    app        open the desktop app (window + menu bar icon)
    install    set up your players, start Tally now and at every login
    run        run in the foreground (what the login item runs)
    open       open the browse page
    status     show what Tally sees right now
    setup      only configure players (add --undo to reverse)
    uninstall  stop starting at login (your history is kept)
    parse      show how a file name is understood
    import-iina  add what you watched in IINA before Tally was running
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import signal
import sys
import time
import urllib.request
import webbrowser

from . import __version__, paths, service

def _url(port: int) -> str:
    return f"http://127.0.0.1:{port}/"


def _ping(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(_url(port) + "api/ping", timeout=2) as resp:
            data = json.loads(resp.read())
            return data if data.get("app") == "tally" else None
    except (OSError, ValueError):
        return None


def _setup_logging(verbose: bool) -> None:
    handler = logging.handlers.RotatingFileHandler(paths.log_path(), maxBytes=1_000_000, backupCount=2,
                                                   encoding="utf-8")
    handlers: list[logging.Handler] = [handler]
    if sys.stderr and sys.stderr.isatty():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def cmd_run(args) -> int:
    from .engine import Engine

    _setup_logging(args.verbose)
    engine = Engine(args.port, lan=args.lan)
    try:
        engine.start()
    except OSError:
        if _ping(engine.port):
            print(f"Tally is already running at {_url(engine.port)}")
            return 0
        logging.getLogger("tally").error("port %s is taken by another program; use --port", engine.port)
        print(f"Port {engine.port} is in use by another program. Try: python -m tally run --port 8738",
              file=sys.stderr)
        return 1
    if sys.stdout and sys.stdout.isatty():
        print(f"Tally is running at {_url(engine.port)}  (Ctrl+C to stop)")
    signal.signal(signal.SIGTERM, engine.stop)
    signal.signal(signal.SIGINT, engine.stop)
    engine.run()
    return 0


def cmd_install(args) -> int:
    from .db import Database
    from .setup_players import setup_all

    db = Database()
    print("Setting up players so they report exact progress:")
    for line in setup_all(db):
        print("  " + line)
    try:
        print(service.install())
    except RuntimeError as exc:
        print(exc)
        return 1
    port = int(db.get_setting("port", str(paths.DEFAULT_PORT)))
    for _ in range(20):
        if _ping(port):
            break
        time.sleep(0.5)
    if _ping(port):
        print(f"Tally is running. Your history: {_url(port)}")
        if not args.no_open:
            webbrowser.open(_url(port))
    else:
        print(f"Tally was installed but hasn't answered yet. Check {paths.log_path()}")
    return 0


def cmd_uninstall(args) -> int:
    from .db import Database
    from .setup_players import setup_all

    print(service.uninstall())
    if args.players:
        for line in setup_all(Database(), undo=True):
            print("  " + line)
    print(f"Your history is still saved in {paths.db_path()}")
    return 0


def cmd_setup(args) -> int:
    from .db import Database
    from .setup_players import setup_all

    for line in setup_all(Database(), undo=args.undo):
        print(line)
    return 0


def cmd_open(args) -> int:
    from .db import Database

    port = int(Database().get_setting("port", str(paths.DEFAULT_PORT)))
    if not _ping(port):
        print("Tally isn't running. Start it with: python -m tally install")
        return 1
    webbrowser.open(_url(port))
    return 0


def cmd_status(args) -> int:
    from .db import Database
    from .players import all_sources, poll_all

    db = Database()
    port = int(db.get_setting("port", str(paths.DEFAULT_PORT)))
    running = _ping(port)
    print(f"Tally {__version__}")
    print(f"  background service: {'running' if running else 'not running'} "
          f"({'starts at login' if service.installed() else 'not set to start at login'})")
    print(f"  page:      {_url(port)}")
    print(f"  data file: {db.path}")
    n = db.one("SELECT COUNT(*) AS n FROM watches")["n"]
    print(f"  history:   {n} entries")
    sources = all_sources(db)
    states = poll_all(sources)
    print("Players:")
    for s in sources:
        print(f"  {s.label:28} {s.status:10} {s.detail}")
    if states:
        print("Playing now:")
        for st in states:
            pos = f"{st.position:.0f}s / {st.duration:.0f}s" if st.position is not None and st.duration else "position unknown"
            print(f"  [{st.player}] {os.path.basename(st.path)} ({pos})")
    return 0


def cmd_stop(args) -> int:
    from .db import Database

    port = int(Database().get_setting("port", str(paths.DEFAULT_PORT)))
    if sys.platform == "darwin" and service.installed():
        service._launchctl("bootout", f"gui/{os.getuid()}/{service.LABEL}")
    if _ping(port):
        req = urllib.request.Request(_url(port) + "api/quit", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=3).read()
        except OSError:
            pass
    print("Tally stopped" + (" (it starts again at next login)" if service.installed() else ""))
    return 0


def cmd_app(args) -> int:
    from .desktop import main as desktop_main

    return desktop_main(["--background"] if args.background else [])


def cmd_import_iina(args) -> int:
    from . import iina_import
    from .db import Database
    from .matcher import Matcher

    if not iina_import.available():
        print("No IINA history found")
        return 1
    db = Database()
    matcher = Matcher(db)
    added = iina_import.import_history(db, matcher)
    db.set_setting("iina_imported", "1")
    print(f"Imported {added} entries. Titles are looked up the next time Tally runs.")
    return 0


def cmd_parse(args) -> int:
    from .parser import parse

    for name in args.names:
        print(json.dumps(parse(name).to_dict(), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tally", description="A quiet, local log of what you watch.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("app", help="open the desktop app")
    p.add_argument("--background", action="store_true", help="start with no window")
    p.set_defaults(func=cmd_app)

    p = sub.add_parser("run", help="run in the foreground")
    p.add_argument("--port", type=int)
    p.add_argument("--lan", action="store_true", help="let other devices on your network open the page")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("install", help="set up players and start at login")
    p.add_argument("--no-open", action="store_true", help="don't open the browser afterwards")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("uninstall", help="stop starting at login (history is kept)")
    p.add_argument("--players", action="store_true", help="also undo the player settings")
    p.set_defaults(func=cmd_uninstall)

    p = sub.add_parser("setup", help="configure players only")
    p.add_argument("--undo", action="store_true")
    p.set_defaults(func=cmd_setup)

    sub.add_parser("open", help="open the browse page").set_defaults(func=cmd_open)
    sub.add_parser("status", help="show what Tally sees").set_defaults(func=cmd_status)
    sub.add_parser("stop", help="stop the background app").set_defaults(func=cmd_stop)

    sub.add_parser("import-iina", help="import IINA's playback history").set_defaults(func=cmd_import_iina)

    p = sub.add_parser("parse", help="show how file names are understood")
    p.add_argument("names", nargs="+")
    p.set_defaults(func=cmd_parse)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
