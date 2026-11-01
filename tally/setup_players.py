"""One-time configuration so players report their exact position to Tally.

* IINA: adds mpv option input-ipc-server (Settings > Advanced > Additional mpv options)
* mpv: adds input-ipc-server to mpv.conf
* VLC: turns on the web interface on 127.0.0.1 with a password
* MPC-HC / MPC-BE (Windows): turns on the localhost web interface

Every change is idempotent, and `tally setup --undo` reverses it.
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import paths
from .db import Database
from .players.vlc import DEFAULT_PASSWORD, DEFAULT_PORT

IINA_DOMAIN = "com.colliderli.iina"


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=20)


# ---- IINA ---------------------------------------------------------------

def _iina_installed() -> bool:
    return sys.platform == "darwin" and any(
        Path(p).exists() for p in ("/Applications/IINA.app", str(Path.home() / "Applications/IINA.app")))


def _iina_prefs() -> dict:
    out = _run(["defaults", "export", IINA_DOMAIN, "-"])
    if out.returncode != 0 or not out.stdout:
        return {}
    return plistlib.loads(out.stdout)


def _plist_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write_iina_options(options: list[list[str]]) -> None:
    literal = "(" + ", ".join(f"({_plist_string(k)}, {_plist_string(v)})" for k, v in options) + ")"
    _run(["defaults", "write", IINA_DOMAIN, "userOptions", literal])


def setup_iina(db: Database, undo: bool = False) -> str:
    if not _iina_installed():
        return "IINA: not installed, skipped"
    prefs = _iina_prefs()
    options = [list(o) for o in prefs.get("userOptions", []) if isinstance(o, (list, tuple)) and len(o) == 2]
    ipc = paths.mpv_ipc_path()
    others = [o for o in options if o[0] != "input-ipc-server"]
    if undo:
        _write_iina_options(others)
        return "IINA: removed input-ipc-server option"
    current = [o for o in options if o[0] == "input-ipc-server"]
    if current and current[0][1] == ipc and prefs.get("enableAdvancedSettings"):
        return "IINA: already set up"
    if current and current[0][1] != ipc:
        # Something else already uses a socket; keep it and have Tally listen there
        db.set_setting("mpv_ipc", current[0][1])
        return f"IINA: using existing input-ipc-server={current[0][1]}"
    _write_iina_options(others + [["input-ipc-server", ipc]])
    _run(["defaults", "write", IINA_DOMAIN, "enableAdvancedSettings", "-bool", "true"])
    running = _run(["pgrep", "-x", "IINA"]).returncode == 0
    note = " Quit and reopen IINA once for it to take effect." if running else ""
    return f"IINA: enabled position reporting.{note}"


# ---- mpv ----------------------------------------------------------------

def _mpv_conf() -> Path:
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", "")) / "mpv" / "mpv.conf"
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "mpv" / "mpv.conf"


def setup_mpv(db: Database, undo: bool = False) -> str:
    conf = _mpv_conf()
    if not shutil.which("mpv") and not conf.parent.exists():
        return "mpv: not installed, skipped"
    text = conf.read_text(encoding="utf-8") if conf.exists() else ""
    marker = "# added by Tally"
    lines = [line for line in text.splitlines() if marker not in line]
    if undo:
        conf.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return "mpv: removed Tally line from mpv.conf"
    if marker in text:
        return "mpv: already set up"
    for line in lines:
        m = re.match(r"^\s*input-ipc-server\s*=\s*(\S.*?)\s*(?:#.*)?$", line)
        if m:
            db.set_setting("mpv_ipc", os.path.expanduser(m.group(1)))
            return f"mpv: using existing input-ipc-server={m.group(1)}"
    conf.parent.mkdir(parents=True, exist_ok=True)
    lines.append(f"input-ipc-server={paths.mpv_ipc_path()}  {marker}")
    conf.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f"mpv: added input-ipc-server to {conf}"


# ---- VLC ----------------------------------------------------------------

def _vlcrc() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Preferences" / "org.videolan.vlc" / "vlcrc"
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", "")) / "vlc" / "vlcrc"
    return Path.home() / ".config" / "vlc" / "vlcrc"


def _vlc_installed() -> bool:
    if sys.platform == "darwin":
        return Path("/Applications/VLC.app").exists() or (Path.home() / "Applications/VLC.app").exists()
    if sys.platform.startswith("win"):
        return any(Path(os.environ.get(v, ""), "VideoLAN", "VLC", "vlc.exe").exists()
                   for v in ("ProgramFiles", "ProgramFiles(x86)"))
    return bool(shutil.which("vlc"))


def _set_vlc_option(lines: list[str], key: str, value: str) -> list[str]:
    pattern = re.compile(rf"^#?\s*{re.escape(key)}=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}"
            return lines
    lines.append(f"{key}={value}")
    return lines


def _get_vlc_option(lines: list[str], key: str) -> str | None:
    for line in lines:
        m = re.match(rf"^{re.escape(key)}=(.*)$", line)
        if m:
            return m.group(1).strip()
    return None


def setup_vlc(db: Database, undo: bool = False) -> str:
    rc = _vlcrc()
    if not _vlc_installed() and not rc.exists():
        return "VLC: not installed, skipped"
    lines = rc.read_text(encoding="utf-8", errors="replace").splitlines() if rc.exists() else []
    extra = [i for i in (_get_vlc_option(lines, "extraintf") or "").split(":") if i]
    if undo:
        extra = [i for i in extra if i != "http"]
        lines = _set_vlc_option(lines, "extraintf", ":".join(extra))
        rc.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return "VLC: turned off web interface"
    password = _get_vlc_option(lines, "http-password") or DEFAULT_PASSWORD
    port = _get_vlc_option(lines, "http-port") or str(DEFAULT_PORT)
    if "http" not in extra:
        extra.append("http")
    lines = _set_vlc_option(lines, "extraintf", ":".join(extra))
    lines = _set_vlc_option(lines, "http-password", password)
    lines = _set_vlc_option(lines, "http-port", port)
    lines = _set_vlc_option(lines, "http-host", "127.0.0.1")
    rc.parent.mkdir(parents=True, exist_ok=True)
    rc.write_text("\n".join(lines) + "\n", encoding="utf-8")
    db.set_setting("vlc_password", password)
    db.set_setting("vlc_port", port)
    return f"VLC: web interface on 127.0.0.1:{port}. Restart VLC if it is open."


# ---- MPC-HC / MPC-BE (Windows) ------------------------------------------

def setup_mpc(undo: bool = False) -> str:
    if not sys.platform.startswith("win"):
        return "MPC: Windows only, skipped"
    import winreg  # type: ignore

    done = []
    for name, key_path in (("MPC-HC", r"Software\MPC-HC\MPC-HC\Settings"), ("MPC-BE", r"Software\MPC-BE\WebServer")):
        try:
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path.rsplit("\\", 1)[0])
        except OSError:
            continue  # not installed
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, "EnableWebServer", 0, winreg.REG_DWORD, 0 if undo else 1)
            if not undo:
                winreg.SetValueEx(key, "WebServerPort", 0, winreg.REG_DWORD, 13579)
                winreg.SetValueEx(key, "WebServerLocalhostOnly", 0, winreg.REG_DWORD, 1)
        done.append(name)
    if not done:
        return "MPC: not installed, skipped"
    return f"{' and '.join(done)}: web interface {'off' if undo else 'on (localhost:13579)'}. Restart the player."


def setup_all(db: Database, undo: bool = False) -> list[str]:
    results = []
    for fn in (setup_iina, setup_mpv, setup_vlc):
        try:
            results.append(fn(db, undo=undo))
        except Exception as exc:  # keep going with the other players
            results.append(f"{fn.__name__}: failed ({exc})")
    for fn in (setup_mpc,):
        try:
            results.append(fn(undo=undo))
        except Exception as exc:  # keep going with the other players
            results.append(f"{fn.__name__}: failed ({exc})")
    return results
