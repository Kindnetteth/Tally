"""mpv and IINA, over mpv's JSON IPC (input-ipc-server)."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse

from .. import paths
from . import PlaybackState, Source

PROPS = ["path", "time-pos", "duration", "pause", "idle-active", "working-directory", "pid"]


class _Conn:
    def __init__(self, path: str):
        self.buf = b""
        if sys.platform.startswith("win"):
            self.pipe = open(path, "r+b", buffering=0)  # noqa: SIM115 - closed in close()
            self.sock = None
        else:
            self.pipe = None
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(2)
            try:
                self.sock.connect(path)
            except OSError:
                self.sock.close()
                raise

    def send(self, data: bytes) -> None:
        if self.sock:
            self.sock.sendall(data)
        else:
            self.pipe.write(data)

    def recv_line(self) -> bytes:
        while b"\n" not in self.buf:
            chunk = self.sock.recv(65536) if self.sock else self.pipe.read(1)
            if not chunk:
                raise ConnectionError("mpv closed the connection")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return line

    def close(self) -> None:
        if self.sock:
            self.sock.close()
        if self.pipe:
            self.pipe.close()


def query(path: str, props: list[str]) -> dict:
    conn = _Conn(path)
    try:
        payload = b"".join(
            json.dumps({"command": ["get_property", p], "request_id": i + 1}).encode() + b"\n"
            for i, p in enumerate(props))
        conn.send(payload)
        result: dict = {}
        pending = set(range(1, len(props) + 1))
        deadline = time.monotonic() + 3
        while pending and time.monotonic() < deadline:
            msg = json.loads(conn.recv_line() or b"{}")
            rid = msg.get("request_id")
            if rid in pending:
                pending.discard(rid)
                if msg.get("error") == "success":
                    result[props[rid - 1]] = msg.get("data")
        return result
    finally:
        conn.close()


_process_names: dict[int, str] = {}


def _player_name(pid: int | None) -> str:
    if not pid:
        return "mpv"
    if pid not in _process_names:
        name = "mpv"
        if sys.platform == "darwin":
            try:
                comm = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True,
                                      text=True, timeout=3).stdout
                if "IINA" in comm:
                    name = "IINA"
            except (OSError, subprocess.SubprocessError):
                pass
        _process_names[pid] = name
    return _process_names[pid]


def resolve_path(path: str, cwd: str | None) -> str:
    if path.startswith("file://"):
        return urllib.parse.unquote(urllib.parse.urlparse(path).path)
    if "://" in path:
        return path
    if not os.path.isabs(path) and cwd:
        return os.path.normpath(os.path.join(cwd, path))
    return path


class MpvSource(Source):
    name = "mpv"
    label = "IINA / mpv"

    def __init__(self, db=None, ipc_path: str | None = None):
        super().__init__()
        self.db = db
        self._ipc_path = ipc_path

    @property
    def ipc_path(self) -> str:
        if self._ipc_path:
            return self._ipc_path
        custom = self.db.get_setting("mpv_ipc") if self.db is not None else None
        return custom or paths.mpv_ipc_path()

    def poll(self) -> list[PlaybackState]:
        if not sys.platform.startswith("win") and not os.path.exists(self.ipc_path):
            self.status, self.detail = "waiting", "Not running"
            return []
        try:
            props = query(self.ipc_path, PROPS)
        except (OSError, ConnectionError, ValueError):
            self.status, self.detail = "waiting", "Not running"
            return []
        player = _player_name(props.get("pid"))
        self.status = "connected"
        path = props.get("path")
        if not path or props.get("idle-active"):
            self.detail = f"{player} open, nothing playing"
            return []
        path = resolve_path(path, props.get("working-directory"))
        self.detail = f"{player}: {os.path.basename(path)}"
        return [PlaybackState(player=player, path=path, position=props.get("time-pos"),
                              duration=props.get("duration"), paused=props.get("pause"))]
