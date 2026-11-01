"""MPC-HC and MPC-BE on Windows, through their web interface (port 13579)."""

from __future__ import annotations

import html
import re
import urllib.error
import urllib.request

from . import PlaybackState, Source

PORT = 13579


def _var(page: str, name: str) -> str | None:
    m = re.search(rf'<p id="{name}">(.*?)</p>', page, re.S)
    return html.unescape(m.group(1)).strip() if m else None


class MpcSource(Source):
    name = "mpc"
    label = "MPC-HC / MPC-BE"

    def poll(self) -> list[PlaybackState]:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/variables.html", timeout=2) as resp:
                page = resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError):
            self.status, self.detail = "waiting", "Not running"
            return []
        self.status = "connected"
        state = _var(page, "state")  # -1 none, 0 stopped, 1 paused, 2 playing
        path = _var(page, "filepath")
        if state not in ("1", "2") or not path:
            self.detail = "MPC open, nothing playing"
            return []
        try:
            position = int(_var(page, "position") or 0) / 1000
            duration = int(_var(page, "duration") or 0) / 1000 or None
        except ValueError:
            position, duration = None, None
        self.detail = f"MPC: {path.rsplit(chr(92), 1)[-1]}"
        return [PlaybackState(player="MPC", path=path, position=position, duration=duration,
                              paused=state == "1")]
