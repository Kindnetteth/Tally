"""Sources that report what is playing right now.

Each source returns a list of PlaybackState. Sources that talk to the player
(mpv/IINA, VLC, MPC) know the exact position; the open-file fallback only
knows which file is open and Tally estimates progress from time spent.
"""

from __future__ import annotations

import logging
import sys
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

    def __init__(self):
        self.status = "waiting"  # 'connected', 'waiting', 'off', 'error'
        self.detail = ""

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


def poll_all(sources: list[Source]) -> list[PlaybackState]:
    """Exact sources win over the open-file fallback for the same file."""
    states: list[PlaybackState] = []
    for source in sources:
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
