from __future__ import annotations

import json
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from clipper.http import Http
from clipper.models import Candidate

ASSETS = Path(__file__).parent / "assets"
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg absent")


@dataclass
class Recorded:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)

    def form(self) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(self.body.decode()).items()}


Reply = tuple[int, dict[str, str], bytes | str | dict | list]


@dataclass
class FakeServer:
    """Local HTTP server standing in for Twitch, Google, TikTok, Telegram, GitHub or Anthropic."""

    routes: dict[tuple[str, str], Callable[[Recorded], Reply]] = field(default_factory=dict)
    requests: list[Recorded] = field(default_factory=list)
    url: str = ""

    def route(self, method: str, path: str, handler: Callable[[Recorded], Reply] | Reply) -> None:
        self.routes[(method, path)] = handler if callable(handler) else (lambda _req, reply=handler: reply)

    def calls(self, method: str, path: str) -> list[Recorded]:
        return [r for r in self.requests if r.method == method and r.path == path]


@pytest.fixture
def server():
    fake = FakeServer()

    class Handler(BaseHTTPRequestHandler):
        def _handle(self) -> None:
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            request = Recorded(self.command, parsed.path, parse_qs(parsed.query), {k.lower(): v for k, v in self.headers.items()}, body)
            fake.requests.append(request)
            handler = fake.routes.get((self.command, parsed.path))
            if handler is None:
                status, headers, payload = 404, {}, {"error": f"no route {self.command} {parsed.path}"}
            else:
                status, headers, payload = handler(request)
            if isinstance(payload, (dict, list)):
                data = json.dumps(payload).encode()
                headers = {"Content-Type": "application/json", **headers}
            else:
                data = payload.encode() if isinstance(payload, str) else payload
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_DELETE = _handle

        def log_message(self, format: str, *args: Any) -> None:
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    fake.url = f"http://127.0.0.1:{httpd.server_port}"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield fake
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def http() -> Http:
    return Http(retries=2, sleep=lambda _s: None)


def make_candidate(key: str = "twitch:Clip1", login: str = "streamer", **values: Any) -> Candidate:
    defaults: dict[str, Any] = dict(
        key=key,
        source="twitch",
        url=f"https://clips.twitch.tv/{key.split(':', 1)[-1]}",
        title="Il rage quit en plein live",
        streamer=login.capitalize(),
        streamer_login=login,
        channel_url=f"https://www.twitch.tv/{login}",
        views=1000,
        duration=30.0,
        created_at=NOW - timedelta(hours=2),
    )
    defaults.update(values)
    return Candidate(**defaults)


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def media(tmp_path_factory) -> dict[str, Path]:
    """Synthetic sources: gameplay with a facecam, a full-frame camera, no face at all."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg absent")
    folder = tmp_path_factory.mktemp("media")
    face = ASSETS / "face.jpg"
    paths = {
        "facecam": folder / "facecam.mp4",
        "fullcam": folder / "fullcam.mp4",
        "noface": folder / "noface.mp4",
        "silent": folder / "silent.mp4",
    }
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=6", "-loop", "1", "-i", str(face),
        "-f", "lavfi", "-i", "sine=f=330:d=6",
        "-filter_complex", "[1:v]scale=230:270[cam];[0:v][cam]overlay=24:426:shortest=1,format=yuv420p[v]",
        "-map", "[v]", "-map", "2:a", "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(paths["facecam"]),
    )  # fmt: skip
    ffmpeg(
        "-loop", "1", "-i", str(face), "-f", "lavfi", "-i", "sine=f=220:d=6",
        "-filter_complex", "[0:v]scale=-2:720,pad=1280:720:(ow-iw)/2:0:color=0x334455,format=yuv420p[v]",
        "-map", "[v]", "-map", "1:a", "-t", "6", "-r", "30", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(paths["fullcam"]),
    )  # fmt: skip
    ffmpeg(
        "-f", "lavfi", "-i", "mandelbrot=s=1280x720:r=30", "-f", "lavfi", "-i", "sine=f=440:d=6",
        "-t", "6", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(paths["noface"]),
    )  # fmt: skip
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=25:d=5", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(paths["silent"]))
    return paths
