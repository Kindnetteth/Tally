"""Sources that report what is playing right now.

Each source returns a list of PlaybackState. Sources that talk to the player
(mpv/IINA, VLC, MPC) know the exact position; the open-file fallback only
knows which file is open and Tally estimates progress from time spent.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class PlaybackState:
    player: str
    path: str
    position: float | None = None   # seconds
    duration: float | None = None   # seconds
    paused: bool | None = None      # None = unknown
    exact: bool = True              # position came from the player itself


class Source:
    name = "source"
    label = "Source"
    idle_interval = 0.0  # seconds between polls while nothing is playing (0 = every time)

    def __init__(self):
        self.status = "waiting"  # 'connected', 'waiting', 'off', 'error'
        self.detail = ""
        self.last_poll = 0.0

    def poll(self) -> list[PlaybackState]:
        raise NotImplementedError

    def info(self) -> dict:
        return {"name": self.name, "label": self.label, "status": self.status, "detail": self.detail}


def all_sources(db) -> list[Source]:
    from .mpv import MpvSource
    from .vlc import VlcSource
    sources: list[Source] = [MpvSource(db), VlcSource(db)]
    if sys.platform.startswith("win"):
        from .mpc import MpcSource
        from .openfiles import WindowsOpenFiles
        sources += [MpcSource(), WindowsOpenFiles()]
    elif sys.platform == "darwin":
        from .openfiles import MacOpenFiles
        sources.append(MacOpenFiles())
    return sources


def poll_all(sources: list[Source], idle: bool = False) -> list[PlaybackState]:
    """Exact sources win over the open-file fallback for the same file.

    While idle (nothing playing), sources that are costly to ask are polled less often.
    """
    states: list[PlaybackState] = []
    now = time.monotonic()
    for source in sources:
        if idle and source.idle_interval and now - source.last_poll < source.idle_interval:
            continue
        source.last_poll = now
        try:
            found = source.poll()
        except Exception as exc:  # a broken player must not stop logging
            log.debug("%s poll failed: %s", source.name, exc, exc_info=True)
            source.status, source.detail = "error", str(exc)
            continue
        for state in found:
            if not state.exact and any(_same_file(state.path, s.path) for s in states):
                continue
            states.append(state)
    return states


def _same_file(a: str, b: str) -> bool:
    if a == b:
        return True
    # Window-title sources only know the file name
    return a.replace("\\", "/").rsplit("/", 1)[-1] == b.replace("\\", "/").rsplit("/", 1)[-1]
