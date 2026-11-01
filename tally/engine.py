"""The part of Tally that watches players and serves the page.

Both the desktop app and `python -m tally run` drive one of these.
"""

from __future__ import annotations

import logging
import threading
import time

from . import __version__, paths
from . import prefs as user_prefs

POLL_PLAYING = 3.0
POLL_IDLE = 5.0
REFRESH_CHECK = 3600  # how often to look for shows whose episode lists are due a refresh

log = logging.getLogger("tally")


class Engine:
    def __init__(self, port: int | None = None, lan: bool = False):
        from .db import Database
        from .matcher import Matcher
        from .players import all_sources
        from .server import App
        from .tracker import Tracker

        self.db = Database()
        self.port = port or int(self.db.get_setting("port", str(paths.DEFAULT_PORT)))
        self.matcher = Matcher(self.db)
        self.tracker = Tracker(self.db, self.matcher)
        self.sources = all_sources(self.db)
        self.app = App(self.db, self.matcher, self.tracker, self.sources, self.port, lan=lan)
        self.app.request_quit = self.stop
        self.server = None
        self._stopping = threading.Event()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> None:
        """Open the port and start matching titles. Raises OSError if the port is taken."""
        from .server import serve

        self.server = serve(self.app)
        self.matcher.start()
        fixed = self.db.recheck_completed(*user_prefs.finish_rule(user_prefs.load(self.db)))
        if fixed:
            log.info("marked %d entries finished under the current rule", fixed)
        log.info("Tally %s running at %s (data: %s)", __version__, self.url, self.db.path)

    def run(self) -> None:
        """Poll the players until stop() is called."""
        from .players import poll_all

        next_refresh = time.monotonic() + 60  # give startup a minute first
        while not self._stopping.is_set():
            if time.monotonic() >= next_refresh:
                next_refresh = time.monotonic() + REFRESH_CHECK
                try:
                    if n := self.matcher.refresh_due():
                        log.info("checking %d shows for new episodes", n)
                except Exception:
                    log.exception("refresh check failed")
            try:
                self.tracker.update(poll_all(self.sources, idle=not self.tracker.sessions))
            except Exception:
                log.exception("poll failed")
            self._stopping.wait(POLL_PLAYING if self.tracker.sessions else POLL_IDLE)
        if self.server:
            self.server.shutdown()
        log.info("stopped")

    def stop(self, *_) -> None:
        self._stopping.set()
