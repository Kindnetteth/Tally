"""VLC, through its built-in web interface (enabled by `tally setup`)."""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request

from . import PlaybackState, Source

DEFAULT_PORT = 8089
DEFAULT_PASSWORD = "tally"


class VlcSource(Source):
    name = "vlc"
    label = "VLC"

    def __init__(self, db=None):
        super().__init__()
        self.db = db

    def _settings(self) -> tuple[int, str]:
        port, password = DEFAULT_PORT, DEFAULT_PASSWORD
        if self.db is not None:
            port = int(self.db.get_setting("vlc_port", str(DEFAULT_PORT)))
            password = self.db.get_setting("vlc_password", DEFAULT_PASSWORD)
        return port, password

    def _get(self, port: int, password: str, path: str):
        token = base64.b64encode(f":{password}".encode()).decode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Authorization": f"Basic {token}"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    def poll(self) -> list[PlaybackState]:
        port, password = self._settings()
        try:
            status = self._get(port, password, "/requests/status.json")
        except urllib.error.HTTPError as exc:
            exc.close()
            self.status = "error"
            self.detail = "Wrong password" if exc.code == 401 else f"HTTP {exc.code}"
            return []
        except (urllib.error.URLError, OSError, ValueError):
            self.status, self.detail = "waiting", "Not running"
            return []
        self.status = "connected"
        state = status.get("state")
        if state not in ("playing", "paused"):
            self.detail = "VLC open, nothing playing"
            return []
        path = None
        try:
            path = _current_uri(self._get(port, password, "/requests/playlist.json"))
        except (urllib.error.URLError, OSError, ValueError):
            pass
        if not path:
            meta = (status.get("information") or {}).get("category", {}).get("meta", {})
            path = meta.get("filename")
        if not path:
            return []
        length = status.get("length") or None
        position = status.get("time")
        if length and isinstance(status.get("position"), (int, float)):
            position = status["position"] * length  # finer than the whole-second 'time'
        self.detail = f"VLC: {path.rsplit('/', 1)[-1]}"
        return [PlaybackState(player="VLC", path=path, position=position, duration=length,
                              paused=state == "paused")]


def _current_uri(node: dict) -> str | None:
    if node.get("current") == "current" and node.get("uri"):
        uri = node["uri"]
        if uri.startswith("file://"):
            parsed = urllib.parse.urlparse(uri)
            path = urllib.parse.unquote(parsed.path)
            if len(path) > 2 and path[0] == "/" and path[2] == ":":  # file:///C:/...
                path = path[1:].replace("/", "\\")
            return path
        return uri
    for child in node.get("children", []) or []:
        found = _current_uri(child)
        if found:
            return found
    return None
