#!/usr/bin/env python3
"""music-web: 局域网网页，控制 Mac 上的受控 Chrome 播放 B 站音乐。

架构：手机浏览器 -> music-web(:8098) -> ssh mac -> music-bridge(:8765) -> CDP -> Chrome
安全：Basic 认证；固定端点；参数白名单；不经 shell 拼接。
"""
from __future__ import annotations

import base64
import hmac
import json
import os
import pathlib
import re
import shlex
import subprocess
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("MW_PORT", "8098"))
# 默认不启用认证，适合「只在家里局域网用」的场景。
# 需要认证时设 MW_AUTH=1 MW_USER=xxx MW_PASSWORD=yyy（密码为空会拒绝所有请求）。
USER = os.environ.get("MW_USER", "media")
PASSWORD = os.environ.get("MW_PASSWORD", "")
AUTH_ENABLED = os.environ.get("MW_AUTH", "0") == "1"
MAC_ALIAS = os.environ.get("MW_MAC", "mac")
BRIDGE = os.environ.get("MW_BRIDGE", "http://127.0.0.1:8765")
HERE = pathlib.Path(__file__).resolve().parent
PAGE = (HERE / "page.html").read_bytes()

BV_RE = re.compile(r"^BV[0-9A-Za-z]{8,14}$")


def bridge(method: str, path: str, timeout: int = 50):
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", MAC_ALIAS,
           "curl", "-s", "-m", str(max(10, timeout - 8)), "-X", method, shlex.quote(BRIDGE + path)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    out = (p.stdout or "").strip()
    if not out:
        return {"ok": False, "error": (p.stderr or "empty response")[:180]}
    try:
        return json.loads(out)
    except Exception:
        return {"ok": False, "error": out[:180]}


class Handler(BaseHTTPRequestHandler):
    server_version = "music-web/1.0"

    def log_message(self, fmt, *args):
        pass

    def _auth_ok(self) -> bool:
        h = self.headers.get("Authorization", "")
        if not h.startswith("Basic "):
            return False
        try:
            raw = base64.b64decode(h[6:]).decode()
        except Exception:
            return False
        u, _, pw = raw.partition(":")
        if not PASSWORD:
            return False
        return hmac.compare_digest(u, USER) and hmac.compare_digest(pw, PASSWORD)

    def _send(self, body: bytes, ctype: str, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8", code)

    def _need_auth(self):
        if not AUTH_ENABLED:
            return True
        if self._auth_ok():
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="music"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self):
        if not self._need_auth():
            return
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                return self._send(PAGE, "text/html; charset=utf-8")
            if u.path == "/api/status":
                return self._json(bridge("GET", "/status"))
            if u.path == "/api/search":
                qs = (q.get("q", [""])[0] or "").strip()[:64]
                try:
                    pg = max(1, min(50, int(q.get("page", ["1"])[0])))
                except Exception:
                    pg = 1
                return self._json(bridge("GET", "/search?q=" + urllib.parse.quote(qs)
                                         + "&page=" + str(pg), timeout=90))
            if u.path == "/api/qr":
                return self._json(bridge("GET", "/qr", timeout=70))
            if u.path == "/api/login":
                return self._json(bridge("GET", "/login", timeout=30))
            return self._json({"ok": False, "error": "not found"}, 404)
        except Exception as e:
            return self._json({"ok": False, "error": str(e)}, 500)

    def do_POST(self):
        if not self._need_auth():
            return
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        length = int(self.headers.get("Content-Length") or 0)
        if length > 0:
            try:
                self.rfile.read(length)
            except Exception:
                pass
        try:
            if u.path == "/api/play":
                return self._json(bridge("POST", "/play"))
            if u.path == "/api/pause":
                return self._json(bridge("POST", "/pause"))
            if u.path == "/api/open":
                bv = q.get("bv", [""])[0]
                if not BV_RE.match(bv):
                    return self._json({"ok": False, "error": "bad bv"}, 400)
                return self._json(bridge("POST", "/open?bv=" + bv, timeout=70))
            if u.path == "/api/volume":
                lvl = int(q.get("level", ["50"])[0])
                lvl = max(0, min(100, lvl))
                return self._json(bridge("GET", f"/volume?level={lvl}"))
            if u.path == "/api/login":
                return self._json(bridge("GET", "/login", timeout=30))
            if u.path == "/api/qr":
                return self._json(bridge("GET", "/qr", timeout=70))
            if u.path == "/api/bt":
                act = q.get("action", ["connect"])[0]
                if act not in ("connect", "disconnect"):
                    return self._json({"ok": False, "error": "bad action"}, 400)
                return self._json(bridge("POST", "/bt?action=" + act, timeout=60))
            return self._json({"ok": False, "error": "not found"}, 404)
        except Exception as e:
            return self._json({"ok": False, "error": str(e)}, 500)


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"music-web on 0.0.0.0:{PORT}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
