"""Local web server for the browse page and its JSON API."""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import socket
import socketserver
import sys
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, metadata, paths
from .api import Api

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"


class App:
    """Everything the request handler needs, shared across threads."""

    def __init__(self, db, matcher, tracker, sources, port: int, lan: bool = False):
        self.db = db
        self.api = Api(db)
        self.matcher = matcher
        self.tracker = tracker
        self.sources = sources
        self.port = port
        self.lan = lan
        self.request_quit = lambda: None


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"Tally/{__version__}"

        def log_message(self, fmt, *args):  # keep the log quiet
            log.debug("%s - %s", self.address_string(), fmt % args)

        # ---- plumbing ---------------------------------------------------

        def _host_ok(self) -> bool:
            """Block DNS-rebinding: only answer to our own host names."""
            if app.lan:
                return True
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
            return host in ("127.0.0.1", "localhost", "::1")

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return True
            return urllib.parse.urlparse(origin).netloc == self.headers.get("Host")

        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, data, status: int = 200):
            body = json.dumps(data, default=str).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8", {"Cache-Control": "no-store"})

        def _error(self, status: int, message: str):
            self._json({"error": message}, status)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            data = json.loads(self.rfile.read(min(length, 1_000_000)) or b"{}")
            return data if isinstance(data, dict) else {}

        # ---- GET --------------------------------------------------------

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self._host_ok():
                return self._error(HTTPStatus.FORBIDDEN, "bad host")
            url = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(url.query)
            path = url.path
            try:
                if path == "/" or path == "/index.html":
                    return self._static("index.html")
                if path.startswith("/static/"):
                    return self._static(path[len("/static/"):])
                if m := re.fullmatch(r"/poster/(\d+)", path):
                    return self._poster(int(m.group(1)))
                if path == "/api/ping":
                    return self._json({"app": "tally", "version": __version__})
                if path == "/api/home":
                    week_start = float(q["week_start"][0]) if "week_start" in q else None
                    return self._json(app.api.home(app.tracker.now_playing(), week_start))
                if path == "/api/shows":
                    return self._json(app.api.shows())
                if path == "/api/movies":
                    return self._json(app.api.movies())
                if m := re.fullmatch(r"/api/titles/(\d+)", path):
                    data = app.api.title(int(m.group(1)))
                    return self._json(data) if data else self._error(404, "not found")
                if path == "/api/history":
                    return self._json(app.api.history(
                        q=q.get("q", [""])[0], limit=int(q.get("limit", ["100"])[0]),
                        before=float(q["before"][0]) if "before" in q else None,
                        since=float(q["since"][0]) if "since" in q else None))
                if path == "/api/search":
                    kind = q.get("kind", ["show"])[0]
                    year = int(q["year"][0]) if q.get("year", [""])[0].isdigit() else None
                    found = metadata.search(kind, q.get("q", [""])[0], year, app.db.get_setting("tmdb_key"))
                    return self._json([c.summary() for c in found[:12]])
                if path == "/api/status":
                    return self._json(self._status())
                if path == "/api/export":
                    body = json.dumps(app.api.export(), indent=1, default=str).encode()
                    return self._send(200, body, "application/json",
                                      {"Content-Disposition": 'attachment; filename="tally-export.json"'})
                return self._error(404, "not found")
            except (ValueError, KeyError) as exc:
                return self._error(400, str(exc))
            except Exception as exc:
                log.exception("GET %s failed", path)
                return self._error(500, str(exc))

        def _static(self, name: str):
            target = (WEB_DIR / name).resolve()
            if WEB_DIR.resolve() not in target.parents or not target.is_file():
                return self._error(404, "not found")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, target.read_bytes(), ctype, {"Cache-Control": "no-cache"})

        def _poster(self, title_id: int):
            row = app.db.one("SELECT poster, poster_type FROM titles WHERE id = ?", (title_id,))
            if not row or not row["poster"]:
                return self._error(404, "no poster")
            self._send(200, row["poster"], row["poster_type"] or "image/jpeg",
                       {"Cache-Control": "max-age=604800"})

        def _status(self) -> dict:
            return {
                "version": __version__, "data_file": str(app.db.path), "port": app.port, "lan": app.lan,
                "sources": [s.info() for s in app.sources],
                "tmdb_key_set": bool(app.db.get_setting("tmdb_key")),
                "unmatched": app.db.one("SELECT COUNT(*) AS n FROM titles WHERE matched = 0")["n"],
                "log_file": str(paths.log_path()),
            }

        # ---- POST -------------------------------------------------------

        def do_POST(self):
            if not self._host_ok() or not self._origin_ok():
                return self._error(HTTPStatus.FORBIDDEN, "cross-site request refused")
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                return self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "send JSON")
            path = urllib.parse.urlparse(self.path).path
            try:
                body = self._body()
                if m := re.fullmatch(r"/api/watches/(\d+)/delete", path):
                    app.api.delete_watch(int(m.group(1)))
                    return self._json({"ok": True})
                if m := re.fullmatch(r"/api/watches/(\d+)/completed", path):
                    app.api.set_completed(int(m.group(1)), bool(body.get("completed")))
                    return self._json({"ok": True})
                if m := re.fullmatch(r"/api/titles/(\d+)/episode", path):
                    app.api.mark_episode(int(m.group(1)), int(body["season"]), int(body["episode"]),
                                         bool(body.get("watched", True)))
                    return self._json({"ok": True})
                if m := re.fullmatch(r"/api/titles/(\d+)/through", path):
                    n = app.api.mark_through(int(m.group(1)), int(body["season"]), int(body["episode"]))
                    return self._json({"ok": True, "marked": n})
                if m := re.fullmatch(r"/api/titles/(\d+)/match", path):
                    title_id = int(m.group(1))
                    kind = body.get("kind", "show")
                    cand = metadata.details(body["source"], str(body["source_id"]), kind,
                                            app.db.get_setting("tmdb_key"))
                    if cand.source == "imdb" and body.get("name"):
                        cand.name = cand.name if cand.name != cand.source_id else body["name"]
                        cand.year = cand.year or body.get("year")
                        cand.poster_url = cand.poster_url or body.get("poster_url")
                    # Episode numbers may need remapping for the new show
                    app.db.execute("UPDATE watches SET season = NULL, episode = raw_episode "
                                   "WHERE title_id = ? AND raw_episode IS NOT NULL AND season IS NOT NULL "
                                   "AND episode != raw_episode", (title_id,))
                    new_id = app.matcher.apply(title_id, cand, locked=True)
                    return self._json({"ok": True, "title_id": new_id})
                if m := re.fullmatch(r"/api/titles/(\d+)/delete", path):
                    app.api.delete_title(int(m.group(1)))
                    return self._json({"ok": True})
                if m := re.fullmatch(r"/api/titles/(\d+)/refresh", path):
                    title_id = int(m.group(1))
                    row = app.db.one("SELECT source, source_id, kind FROM titles WHERE id = ?", (title_id,))
                    if row and row["source"]:
                        cand = metadata.details(row["source"], row["source_id"], row["kind"],
                                                app.db.get_setting("tmdb_key"))
                        app.matcher.apply(title_id, cand)
                    else:
                        app.db.execute("UPDATE titles SET locked = 0 WHERE id = ?", (title_id,))
                        app.matcher.lookup(title_id)
                    return self._json({"ok": True})
                if path == "/api/settings":
                    if "tmdb_key" in body:
                        key = (body["tmdb_key"] or "").strip()
                        if key and not metadata.tmdb_key_works(key):
                            return self._error(400, "TMDB didn't accept that key")
                        app.db.set_setting("tmdb_key", key or None)
                    return self._json({"ok": True})
                if path == "/api/quit":
                    self._json({"ok": True})
                    app.request_quit()
                    return None
                if path == "/api/rematch-unmatched":
                    for row in app.db.query("SELECT id FROM titles WHERE matched = 0"):
                        app.matcher.enqueue(row["id"])
                    return self._json({"ok": True})
                return self._error(404, "not found")
            except (ValueError, KeyError, TypeError) as exc:
                return self._error(400, str(exc))
            except Exception as exc:
                log.exception("POST %s failed", path)
                return self._error(500, str(exc))

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # On macOS/Linux this only lets us rebind right after a restart; a second
    # copy still can't bind while the first is listening. On Windows it would
    # allow two listeners, so use the exclusive flag there instead.
    allow_reuse_address = not sys.platform.startswith("win")

    def server_bind(self):
        if sys.platform.startswith("win") and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        # HTTPServer.server_bind does a reverse DNS lookup that can hang for
        # a long time on macOS; we don't need the host name, so skip it.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def serve(app: App) -> Server:
    host = "0.0.0.0" if app.lan else "127.0.0.1"
    server = Server((host, app.port), make_handler(app))
    threading.Thread(target=server.serve_forever, name="tally-http", daemon=True).start()
    return server
