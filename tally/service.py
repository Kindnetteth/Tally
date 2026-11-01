"""Start Tally automatically at login (LaunchAgent on macOS, Startup folder on Windows).

There are two kinds of login item. The desktop app (Tally.app / Tally.exe)
starts itself with --background. The older command-line install runs
`python -m tally run` from a copy of the package; the desktop app removes
that one the first time it starts.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from . import paths

LABEL = "com.tally.agent"
APP_LABEL = "com.tally.app"
PACKAGE_DIR = Path(__file__).resolve().parent


def deploy() -> Path:
    """Copy the app next to its data and run it from there.

    macOS won't let a login item read ~/Documents, ~/Desktop or ~/Downloads
    without a permission prompt, so the background copy must live elsewhere.
    Running `install` again updates the copy.
    """
    root = paths.data_dir() / "app"
    target = root / "tally"
    if PACKAGE_DIR == target:
        return root
    tmp = root / "tally.new"
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(PACKAGE_DIR, tmp, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.rmtree(target, ignore_errors=True)
    tmp.rename(target)
    return root


def _python(windowless: bool = False) -> str:
    exe = Path(sys.executable)
    if windowless and sys.platform.startswith("win"):
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.exists():
            return str(pythonw)
    return str(exe)


# ---- macOS --------------------------------------------------------------

def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _install_mac() -> str:
    root = deploy()
    plist = {
        "Label": LABEL,
        "ProgramArguments": [_python(), "-m", "tally", "run"],
        "WorkingDirectory": str(root),
        "EnvironmentVariables": {"PYTHONPATH": str(root), "PYTHONUNBUFFERED": "1"},
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 5,
        "StandardOutPath": str(paths.data_dir() / "launchd.out.log"),
        "StandardErrorPath": str(paths.data_dir() / "launchd.err.log"),
    }
    path = _plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{domain}/{LABEL}")
    with open(path, "wb") as fh:
        plistlib.dump(plist, fh)
    res = _launchctl("bootstrap", domain, str(path))
    if res.returncode != 0:
        res = _launchctl("load", "-w", str(path))
        if res.returncode != 0:
            raise RuntimeError(f"launchctl failed: {res.stderr.strip()}")
    return f"Starts at login via {path}"


def _uninstall_mac() -> str:
    _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    path = _plist_path()
    if path.exists():
        path.unlink()
    shutil.rmtree(paths.data_dir() / "app", ignore_errors=True)
    return "Removed login item"


# ---- Windows ------------------------------------------------------------

def _startup_script() -> Path:
    return (Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            / "Startup" / "Tally.vbs")


def _install_windows() -> str:
    script = _startup_script()
    script.parent.mkdir(parents=True, exist_ok=True)
    py = _python(windowless=True).replace('"', '""')
    root = str(deploy()).replace('"', '""')
    script.write_text(
        'Set sh = CreateObject("WScript.Shell")\r\n'
        f'sh.Environment("PROCESS")("PYTHONPATH") = "{root}"\r\n'
        f'sh.CurrentDirectory = "{root}"\r\n'
        f'sh.Run """{py}"" -m tally run", 0, False\r\n',
        encoding="utf-8")
    subprocess.Popen(["wscript.exe", str(script)], close_fds=True)
    return f"Starts at login via {script}"


def _uninstall_windows() -> str:
    script = _startup_script()
    if script.exists():
        script.unlink()
    return "Removed startup item (Tally keeps running until you sign out or run `tally stop`)"


# ---- public -------------------------------------------------------------

def install() -> str:
    if sys.platform == "darwin":
        return _install_mac()
    if sys.platform.startswith("win"):
        return _install_windows()
    raise RuntimeError("Autostart is set up for macOS and Windows. On Linux, run `python -m tally run` "
                       "from your desktop's autostart settings or a systemd user unit.")


def uninstall() -> str:
    if sys.platform == "darwin":
        return _uninstall_mac()
    if sys.platform.startswith("win"):
        return _uninstall_windows()
    return "Nothing to remove"


def installed() -> bool:
    if sys.platform == "darwin":
        return _plist_path().exists()
    if sys.platform.startswith("win"):
        return _startup_script().exists()
    return False


# ---- desktop app ----------------------------------------------------------

def _app_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{APP_LABEL}.plist"


def _app_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--background"]
    return [_python(windowless=True), "-m", "tally.desktop", "--background"]


def app_login_enabled() -> bool:
    if sys.platform == "darwin":
        return _app_plist_path().exists()
    if sys.platform.startswith("win"):
        script = _startup_script()
        return script.exists() and "-m tally run" not in script.read_text(encoding="utf-8", errors="replace")
    return False


def set_app_login(enabled: bool) -> None:
    """Turn the desktop app's login item on or off.

    This only writes or removes the file the system reads at login, so it
    never starts or stops the copy that's running now.
    """
    cmd = _app_command()
    if sys.platform == "darwin":
        path = _app_plist_path()
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            plist = {"Label": APP_LABEL, "ProgramArguments": cmd, "RunAtLoad": True,
                     "ProcessType": "Interactive", "LimitLoadToSessionType": "Aqua"}
            if not getattr(sys, "frozen", False):
                plist["WorkingDirectory"] = str(PACKAGE_DIR.parent)
            with open(path, "wb") as fh:
                plistlib.dump(plist, fh)
        elif path.exists():
            path.unlink()
    elif sys.platform.startswith("win"):
        script = _startup_script()
        if enabled:
            script.parent.mkdir(parents=True, exist_ok=True)
            exe = cmd[0].replace('"', '""')
            cwd = "" if getattr(sys, "frozen", False) else \
                f'sh.CurrentDirectory = "{str(PACKAGE_DIR.parent).replace(chr(34), chr(34) * 2)}"\r\n'
            script.write_text('Set sh = CreateObject("WScript.Shell")\r\n' + cwd +
                              f'sh.Run """{exe}"" {" ".join(cmd[1:])}", 0, False\r\n', encoding="utf-8")
        elif script.exists():
            script.unlink()


def remove_legacy() -> bool:
    """Remove the command-line install's login item. Returns True if there was one."""
    if sys.platform == "darwin":
        if not _plist_path().exists():
            return False
        _uninstall_mac()
        return True
    if sys.platform.startswith("win"):
        script = _startup_script()
        if script.exists() and "-m tally run" in script.read_text(encoding="utf-8", errors="replace"):
            script.unlink()
            return True
    return False
