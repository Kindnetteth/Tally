"""Fallback for players Tally can't talk to: notice which video file they have open.

This catches QuickTime, Infuse, PotPlayer, Movies & TV and friends, or IINA/VLC
before `tally setup` has run. The exact position isn't known, so progress is
estimated from how long the file stayed open (and the runtime from the database).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

from ..parser import VIDEO_EXTENSIONS, is_video_path
from . import PlaybackState, Source

MAC_PLAYERS = {
    # lsof -c prefix -> display name
    "IINA": "IINA", "QuickTime": "QuickTime", "VLC": "VLC", "mpv": "mpv", "Infuse": "Infuse",
    "Movist": "Movist", "Elmedia": "Elmedia", "Plex": "Plex", "Jellyfin": "Jellyfin", "Kodi": "Kodi",
    "SenPlayer": "SenPlayer",
}

WINDOWS_PLAYERS = {
    "vlc.exe": "VLC", "mpv.exe": "mpv", "mpc-hc.exe": "MPC", "mpc-hc64.exe": "MPC", "mpc-be.exe": "MPC",
    "mpc-be64.exe": "MPC", "potplayer.exe": "PotPlayer", "potplayermini.exe": "PotPlayer",
    "potplayermini64.exe": "PotPlayer", "potplayer64.exe": "PotPlayer", "wmplayer.exe": "Windows Media Player",
    "video.ui.exe": "Movies & TV", "microsoft.media.player.exe": "Media Player", "kmplayer.exe": "KMPlayer",
    "kmplayer64x.exe": "KMPlayer", "gom.exe": "GOM Player", "smplayer.exe": "SMPlayer", "kodi.exe": "Kodi",
    "mpvnet.exe": "mpv.net", "daumpotplayermini64.exe": "PotPlayer", "stremio.exe": "Stremio",
}

_IGNORED_DIRS = ("/System/", "/Library/", "/Applications/", "/private/var/", "/usr/")


class MacOpenFiles(Source):
    name = "openfiles"
    label = "Other players (open files)"
    idle_interval = 15.0  # runs lsof, so not every few seconds while nothing plays

    def poll(self) -> list[PlaybackState]:
        cmd = ["lsof", "-w", "-n", "-P", "-Fpcn"]
        for prefix in MAC_PLAYERS:
            cmd += ["-c", prefix]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            self.status, self.detail = "error", str(exc)
            return []
        states: list[PlaybackState] = []
        seen: set[str] = set()
        command = ""
        for line in out.splitlines():
            if line.startswith("c"):
                command = line[1:]
            elif line.startswith("n"):
                path = line[1:]
                if path in seen or not is_video_path(path) or path.startswith(_IGNORED_DIRS):
                    continue
                if "/Library/Caches/" in path or "/.Trash/" in path:
                    continue
                seen.add(path)
                states.append(PlaybackState(player=_mac_label(command), path=path, exact=False))
        self.status = "connected"
        self.detail = ", ".join(sorted({f"{s.player}: {os.path.basename(s.path)}" for s in states})) or "Watching for players"
        return states


def _mac_label(command: str) -> str:
    for prefix, label in MAC_PLAYERS.items():
        if command.startswith(prefix):
            return label
    return command


_TITLE_FILE_RE = re.compile(
    r"([^\\/:*?\"<>|]+\.(?:" + "|".join(sorted(VIDEO_EXTENSIONS)) + r"))(?![a-z0-9])", re.I)


class WindowsOpenFiles(Source):
    name = "openfiles"
    label = "Other players (open files)"
    idle_interval = 15.0

    def poll(self) -> list[PlaybackState]:
        try:
            import psutil  # type: ignore  # optional, gives full paths
        except ImportError:
            psutil = None
        states = self._from_psutil(psutil) if psutil else self._from_window_titles()
        self.status = "connected"
        self.detail = ", ".join(sorted({f"{s.player}: {os.path.basename(s.path)}" for s in states})) or "Watching for players"
        return states

    def _from_psutil(self, psutil) -> list[PlaybackState]:
        states, seen = [], set()
        for proc in psutil.process_iter(["name"]):
            label = WINDOWS_PLAYERS.get((proc.info.get("name") or "").lower())
            if not label:
                continue
            try:
                files = proc.open_files()
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                continue
            for f in files:
                if is_video_path(f.path) and f.path not in seen:
                    seen.add(f.path)
                    states.append(PlaybackState(player=label, path=f.path, exact=False))
        return states or self._from_window_titles()

    def _from_window_titles(self) -> list[PlaybackState]:
        states, seen = [], set()
        for exe, title in _visible_windows():
            label = WINDOWS_PLAYERS.get(exe.lower())
            if not label:
                continue
            m = _TITLE_FILE_RE.search(title)
            if not m:
                continue
            name = m.group(1).strip()
            if name not in seen:
                seen.add(name)
                states.append(PlaybackState(player=label, path=name, exact=False))
        return states


def _visible_windows() -> list[tuple[str, str]]:
    """(process exe name, window title) for every visible top-level window."""
    if not sys.platform.startswith("win"):
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    results: list[tuple[str, str]] = []
    exe_cache: dict[int, str] = {}

    def exe_for(pid: int) -> str:
        if pid in exe_cache:
            return exe_cache[pid]
        name = ""
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            try:
                buf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(len(buf))
                if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    name = os.path.basename(buf.value)
            finally:
                kernel32.CloseHandle(handle)
        exe_cache[pid] = name
        return name

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        results.append((exe_for(pid.value), buf.value))
        return True

    user32.EnumWindows(callback, 0)
    return results
