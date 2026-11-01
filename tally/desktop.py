"""Tally as a desktop app: a small logger with an icon in the menu bar
(macOS) or system tray (Windows), and a window you open when you want it.

The two run as separate processes so the logger stays light. The logger is
plain Python plus the menu bar icon: it watches your players and serves the
page. The window (this same program with --window) is a web view showing that
page; it starts when you open Tally and exits completely when you close it,
taking the browser engine and its memory with it. Logging carries on until
you choose Quit Tally.

Run with --background to start the logger with no window, which is what the
login item does.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from . import __version__, iina_import, paths, service
from . import prefs as user_prefs
from .engine import Engine

log = logging.getLogger("tally")

ASSETS = Path(__file__).parent / "assets"


def _post(port: int, path: str) -> None:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=3).read()
    except OSError:
        pass


def _take_over(port: int, background: bool) -> bool:
    """Deal with a Tally that's already running. Returns False if this copy should exit."""
    from .__main__ import _ping

    # A separate profile (TALLY_HOME) leaves the normal install alone
    removed = not os.environ.get("TALLY_HOME") and service.remove_legacy()
    if removed:
        log.info("removed the command-line login item; the app replaces it")
    running = _ping(port)
    if not running:
        return True
    if running.get("desktop"):
        if not background:
            _post(port, "/api/show")
        return False
    # The command-line version is running: stop it and use its port
    _post(port, "/api/quit")
    for _ in range(40):
        if not _ping(port):
            break
        time.sleep(0.25)
    return True


def _first_run(engine: Engine) -> None:
    from .setup_players import setup_all

    db = engine.db
    if os.environ.get("TALLY_HOME"):
        pass  # a separate profile never touches the real login item
    elif db.get_setting("app_setup_done"):
        if service.app_login_enabled():
            service.set_app_login(True)  # keep the path current if the app was moved
    else:
        try:
            service.set_app_login(True)
        except OSError:
            log.exception("could not add the login item")
        try:
            for line in setup_all(db):
                log.info("setup: %s", line)
        except Exception:
            log.exception("player setup failed")
        db.set_setting("app_setup_done", "1")
    # Pick up what was watched in IINA before Tally was around, once
    if iina_import.available() and not db.get_setting("iina_imported"):
        try:
            iina_import.import_history(db, engine.matcher)
            db.set_setting("iina_imported", "1")
        except Exception:
            log.exception("IINA history import failed")


class Agent:
    """The always-on part: the tracker plus the menu bar / tray icon."""

    def __init__(self, engine: Engine):
        self.engine = engine
        self.window: subprocess.Popen | None = None
        self.show_requested = threading.Event()
        engine.app.request_show = self.open_window
        engine.app.request_quit = self.quit
        engine.app.wait_for_show = self.wait_for_show

    # ---- the window process -------------------------------------------

    def window_open(self) -> bool:
        return self.window is not None and self.window.poll() is None

    def open_window(self, *_):
        if self.window_open():
            self.show_requested.set()  # the window is waiting on /api/ui-wait and comes forward
            return
        cmd = [sys.executable, "--window"] if getattr(sys, "frozen", False) else \
            [sys.executable, "-m", "tally.desktop", "--window"]
        cmd += ["--port", str(self.engine.port)]
        env = dict(os.environ)
        if not getattr(sys, "frozen", False):
            env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
        self.window = subprocess.Popen(cmd, env=env, close_fds=True)

    def wait_for_show(self, timeout: float) -> bool:
        """Called by the window's long poll: True when it should come to the front."""
        if self.show_requested.wait(timeout):
            self.show_requested.clear()
            return True
        return False

    # ---- shared menu actions ------------------------------------------

    def now_playing(self) -> str | None:
        from .api import _WATCH_SQL, watch_dict

        if self.paused():
            return "Logging paused"
        playing = self.engine.tracker.now_playing()
        if not playing:
            return None
        row = self.engine.db.one(_WATCH_SQL + " WHERE w.id = ?", (playing[0]["watch_id"],))
        if not row:
            return None
        w = {**watch_dict(row), "paused": playing[0]["paused"]}
        name = (w["title"] or {}).get("name") or w["filename"]
        if w["season"] is not None and w["episode"] is not None:
            name += f" · S{int(w['season']):02d}E{int(w['episode']):02d}"
        return ("Paused: " if w.get("paused") else "Watching ") + name

    def paused(self) -> bool:
        return bool(user_prefs.load(self.engine.db)["paused"])

    def toggle_pause(self, *_):
        user_prefs.save(self.engine.db, {"paused": not self.paused()})

    def toggle_login(self, *_):
        service.set_app_login(not service.app_login_enabled())

    def quit(self, *_):
        self.engine.stop()
        if self.window_open():
            self.window.terminate()
        self.stop_loop()

    # ---- per platform -------------------------------------------------

    def run(self):
        """Block until Quit. Without a menu bar or tray, just run until the window closes."""
        self.open_window()
        self.window.wait()
        self.engine.stop()

    def stop_loop(self):
        pass


class MacAgent(Agent):
    def run(self):
        import AppKit
        from PyObjCTools import AppHelper

        agent = self
        app = AppKit.NSApplication.sharedApplication()
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

        class TallyAgentDelegate(AppKit.NSObject):
            def applicationShouldHandleReopen_hasVisibleWindows_(self, app, flag):
                agent.open_window()  # Tally opened again from Finder, Spotlight or the Dock
                return True

            def openTally_(self, sender):
                agent.open_window()

            def togglePause_(self, sender):
                agent.toggle_pause()

            def toggleLogin_(self, sender):
                agent.toggle_login()

            def quitTally_(self, sender):
                agent.quit()

            def menuNeedsUpdate_(self, menu):
                agent._playing.setTitle_(agent.now_playing() or "Nothing playing")
                on, off = AppKit.NSControlStateValueOn, AppKit.NSControlStateValueOff
                agent._pause.setState_(on if agent.paused() else off)
                agent._login.setState_(on if service.app_login_enabled() else off)

        self._delegate = TallyAgentDelegate.alloc().init()
        app.setDelegate_(self._delegate)
        menu = AppKit.NSMenu.alloc().init()
        menu.setDelegate_(self._delegate)
        menu.setAutoenablesItems_(False)

        def add(title, action=None, key=""):
            item = menu.addItemWithTitle_action_keyEquivalent_(title, action, key)
            item.setTarget_(self._delegate)
            return item

        self._playing = add("Nothing playing")
        self._playing.setEnabled_(False)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        add("Open Tally", "openTally:", "o")
        self._pause = add("Pause Logging", "togglePause:", "p")
        self._login = add("Open at Login", "toggleLogin:")
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        add("Quit Tally", "quitTally:", "q")

        image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(ASSETS / "menubar.png"))
        image.setSize_((22, 18))
        image.setTemplate_(True)
        self._item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        self._item.button().setImage_(image)
        self._item.button().setToolTip_("Tally")
        self._item.setMenu_(menu)
        AppHelper.runEventLoop(installInterrupt=True)

    def stop_loop(self):
        from PyObjCTools import AppHelper

        AppHelper.callAfter(AppHelper.stopEventLoop)


class WindowsAgent(Agent):
    def run(self):
        import pystray

        menu = pystray.Menu(
            pystray.MenuItem(lambda _: self.now_playing() or "Nothing playing", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open Tally", lambda: self.open_window(), default=True),
            pystray.MenuItem("Pause logging", self.toggle_pause, checked=lambda _: self.paused()),
            pystray.MenuItem("Start at login", self.toggle_login, checked=lambda _: service.app_login_enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Tally", self.quit),
        )
        self._icon = pystray.Icon("Tally", self._image(user_prefs.load(self.engine.db)), "Tally", menu)
        self.engine.app.prefs_changed = lambda prefs: setattr(self._icon, "icon", self._image(prefs))
        self._icon.run()

    @staticmethod
    def _image(prefs):
        from PIL import Image

        return Image.open(ASSETS / f"icon-{user_prefs.icon_name(prefs)}.png")

    def stop_loop(self):
        icon = getattr(self, "_icon", None)
        if icon:
            icon.stop()


# ---- the window process ------------------------------------------------------

class WindowBridge:
    """Called from the page (window.pywebview.api) for things only the window process can do."""

    def set_icon(self, name: str) -> None:
        _set_dock_icon(name)


def _set_dock_icon(name: str) -> None:
    path = ASSETS / f"icon-{name}.png"
    if sys.platform != "darwin" or not path.is_file():
        return
    import AppKit
    from PyObjCTools import AppHelper

    def apply():
        image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(path))
        AppKit.NSApplication.sharedApplication().setApplicationIconImage_(image)
    AppHelper.callAfter(apply)


def _window_color(prefs: dict) -> str:
    """Background shown before the page paints, so a dark theme doesn't flash white."""
    theme = prefs["theme"]
    if theme == "midnight":
        return "#000000"
    if theme in ("dark", "glow"):
        return "#111115"
    if theme == "system" and sys.platform == "darwin":
        try:
            out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True)
            if "Dark" in out.stdout:
                return "#111115"
        except OSError:
            pass
    return "#f6f6f8"


def run_window(port: int) -> int:
    """The window: shows the logger's page, and exits when you close it."""
    import webview

    from .db import Database

    url = f"http://127.0.0.1:{port}/"
    prefs = user_prefs.load(Database())
    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window("Tally", url + "#restore", width=1200, height=820, min_size=(400, 500),
                                   background_color=_window_color(prefs), js_api=WindowBridge())

    def come_forward_when_asked():
        # Long poll: the logger answers when Tally is opened again while this window is up
        while True:
            try:
                with urllib.request.urlopen(url + "api/ui-wait", timeout=40) as resp:
                    if json.loads(resp.read()).get("show"):
                        window.show()
                        window.restore()
            except OSError:
                if window.events.closed.wait(5):
                    return
            except ValueError:
                pass

    def started():
        _set_dock_icon(user_prefs.icon_name(prefs))
        threading.Thread(target=come_forward_when_asked, name="tally-ui-wait", daemon=True).start()

    webview.start(started, private_mode=False, storage_path=str(paths.data_dir() / "webview"),
                  icon=str(ASSETS / "icon.png"))
    return 0


# ---- entry point -------------------------------------------------------------

def _setup_logging(verbose: bool) -> None:
    from .__main__ import _setup_logging as setup

    setup(verbose)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="Tally")
    parser.add_argument("--background", action="store_true", help="start with no window (used at login)")
    parser.add_argument("--window", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int)
    parser.add_argument("-v", "--verbose", action="store_true")
    # macOS adds -psn_… when an app is opened from Finder on older systems
    args, _ = parser.parse_known_args(argv)

    from .db import Database

    port = args.port or int(Database().get_setting("port", str(paths.DEFAULT_PORT)))
    if args.window:
        return run_window(port)

    _setup_logging(args.verbose)
    if not _take_over(port, args.background):
        return 0

    engine = Engine(port)
    engine.app.desktop = True
    try:
        engine.start()
    except OSError:
        log.error("port %s is taken by another program", port)
        _show_error(f"Another program is using port {port}. Quit it and open Tally again.")
        return 1

    threading.Thread(target=_first_run, args=(engine,), name="tally-setup", daemon=True).start()
    runner = threading.Thread(target=engine.run, name="tally-engine", daemon=True)
    runner.start()

    if sys.platform == "darwin":
        agent: Agent = MacAgent(engine)
    elif sys.platform.startswith("win"):
        agent = WindowsAgent(engine)
    else:
        agent = Agent(engine)
    if not args.background and sys.platform in ("darwin", "win32"):
        agent.open_window()
    agent.run()
    engine.stop()
    runner.join(timeout=5)
    log.info("Tally %s closed", __version__)
    return 0


def _show_error(message: str) -> None:
    if sys.platform == "darwin":
        subprocess.run(["osascript", "-e", f'display alert "Tally can\'t start" message "{message}"'])
    else:
        print(message, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
