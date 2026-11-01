import json
import os
import socket
import socketserver
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from tally.players.mpc import _var
from tally.players.mpv import MpvSource, resolve_path
from tally.players.vlc import VlcSource


class FakeMpv:
    """Answers get_property requests like mpv's JSON IPC does."""

    def __init__(self, path, props):
        self.props = props
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        self.sock.listen(4)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                buf = b""
                conn.sendall(b'{"event":"property-change"}\n')  # unsolicited events are ignored
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        req = json.loads(line)
                        name = req["command"][1]
                        if name in self.props:
                            resp = {"data": self.props[name], "error": "success", "request_id": req["request_id"]}
                        else:
                            resp = {"error": "property unavailable", "request_id": req["request_id"]}
                        conn.sendall(json.dumps(resp).encode() + b"\n")

    def close(self):
        self.sock.close()


@unittest.skipIf(os.name == "nt", "unix sockets")
class MpvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir="/tmp")
        self.path = os.path.join(self.tmp, "s")

    def test_reads_position(self):
        fake = FakeMpv(self.path, {"path": "ep.mkv", "working-directory": "/videos", "time-pos": 61.5,
                                   "duration": 1440.0, "pause": False, "idle-active": False})
        try:
            src = MpvSource(ipc_path=self.path)
            (state,) = src.poll()
            self.assertEqual(state.path, "/videos/ep.mkv")
            self.assertEqual((state.position, state.duration, state.paused, state.exact), (61.5, 1440.0, False, True))
            self.assertEqual(src.status, "connected")
        finally:
            fake.close()

    def test_idle(self):
        fake = FakeMpv(self.path, {"idle-active": True})
        try:
            self.assertEqual(MpvSource(ipc_path=self.path).poll(), [])
        finally:
            fake.close()

    def test_not_running(self):
        src = MpvSource(ipc_path=self.path)
        self.assertEqual(src.poll(), [])
        self.assertEqual(src.status, "waiting")

    def test_resolve_path(self):
        self.assertEqual(resolve_path("file:///a/b%20c.mkv", None), "/a/b c.mkv")
        self.assertEqual(resolve_path("https://x/y.m3u8", "/z"), "https://x/y.m3u8")


class FakeVlcHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.headers.get("Authorization") != "Basic OnRhbGx5":  # ":tally"
            self.send_response(401)
            self.end_headers()
            return
        if self.path == "/requests/status.json":
            body = {"state": "playing", "time": 120, "length": 2400, "position": 0.05,
                    "information": {"category": {"meta": {"filename": "x.mkv"}}}}
        else:
            body = {"children": [{"name": "Playlist", "children": [
                {"name": "a", "uri": "file:///Movies/Other.mkv"},
                {"name": "b", "uri": "file:///Movies/Arrival%20(2016).mkv", "current": "current"}]}]}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class FastServer(HTTPServer):
    def server_bind(self):  # skip the slow reverse DNS lookup
        socketserver.TCPServer.server_bind(self)


def start_vlc():
    server = FastServer(("127.0.0.1", 0), FakeVlcHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def stop(server):
    server.shutdown()
    server.server_close()


class FakeDb:
    def __init__(self, port):
        self.values = {"vlc_port": str(port), "vlc_password": "tally"}

    def get_setting(self, key, default=None):
        return self.values.get(key, default)


class VlcTests(unittest.TestCase):
    def test_reads_current_file(self):
        server = start_vlc()
        try:
            src = VlcSource(FakeDb(server.server_address[1]))
            (state,) = src.poll()
            self.assertEqual(state.path, "/Movies/Arrival (2016).mkv")
            self.assertEqual((state.position, state.duration, state.paused), (120.0, 2400, False))
        finally:
            stop(server)

    def test_wrong_password(self):
        server = start_vlc()
        try:
            db = FakeDb(server.server_address[1])
            db.values["vlc_password"] = "nope"
            src = VlcSource(db)
            self.assertEqual(src.poll(), [])
            self.assertEqual(src.detail, "Wrong password")
        finally:
            stop(server)


class MpcTests(unittest.TestCase):
    def test_variables_page(self):
        page = '<p id="filepath">C:\\Videos\\Show.S01E02.mkv</p><p id="state">2</p><p id="position">5000</p>'
        self.assertEqual(_var(page, "filepath"), "C:\\Videos\\Show.S01E02.mkv")
        self.assertEqual(_var(page, "state"), "2")


if __name__ == "__main__":
    unittest.main()
