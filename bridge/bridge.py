#!/usr/bin/env python3
"""music-bridge: 在 Mac 上通过 CDP 控制受控 Chrome 播放 B 站音乐。

由服务器 music-web 经 ssh 调用，只做固定动作，不接受任意 shell。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CDP_PORT = int(os.environ.get("MB_CDP_PORT", "9222"))
PORT = int(os.environ.get("MB_PORT", "8765"))
# 蓝牙音响地址形如 "AA-BB-CC-DD-EE-FF" 或 "AA:BB:CC:DD:EE:FF"，
# 留空则跳过蓝牙功能（不影响搜索和播放）。
BT_ADDR = os.environ.get("MB_BT_ADDR", "").replace(":", "-").lower()
BT_NAME = os.environ.get("MB_BT_NAME", "")

_hdr_origin = {"Origin": "http://127.0.0.1"}


def cdp_get(path: str):
    req = urllib.request.Request(f"http://127.0.0.1:{CDP_PORT}{path}", headers=_hdr_origin)
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.loads(r.read().decode())


def cdp_eval(page_id: str, expr: str, timeout: float = 8.0):
    import websocket  # websocket-client

    ws = websocket.create_connection(
        f"ws://127.0.0.1:{CDP_PORT}/devtools/page/{page_id}",
        timeout=timeout,
        header=["Origin: http://127.0.0.1"],
    )
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": expr, "returnByValue": True,
                                       "awaitPromise": True, "userGesture": True}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                res = msg.get("result", {})
                if "exceptionDetails" in res:
                    return None
                return res.get("result", {}).get("value")
    finally:
        ws.close()


def tabs():
    out = []
    try:
        listing = cdp_get("/json/list")
    except Exception:
        return []
    for t in listing:
        if t.get("type") != "page":
            continue
        url = t.get("url", "")
        if url.startswith(("devtools://", "edge://", "chrome://")):
            continue
        out.append({"id": t.get("id"), "title": t.get("title"), "url": url})
    return out


def pick_tab(keyword: str = "bilibili"):
    """优先选正常的 B 站页面，跳过登录页；都没有就退到第一个可用标签。"""
    ts = tabs()
    if not ts:
        return None
    for t in ts:
        u = t.get("url") or ""
        if "bilibili.com" in u and "passport" not in u:
            return t
    for t in ts:
        if keyword in (t.get("url") or ""):
            return t
    return ts[0]


def eval_safe(expr: str, timeout: float = 10.0):
    """target id 会随页面导航变化，失败时重新取一次标签再试。"""
    last = None
    for _ in range(3):
        t = pick_tab()
        if not t:
            return None
        try:
            return cdp_eval(t["id"], expr, timeout)
        except Exception as e:
            last = e
            time.sleep(0.9)
    if last:
        raise last
    return None


STATUS_JS = """(() => {
  const logged = document.cookie.indexOf('DedeUserID') >= 0;
  const v = document.querySelector('video');
  if (!v) return {hasVideo: false, title: document.title, logged: logged};
  return {hasVideo: true, paused: v.paused, time: Math.round(v.currentTime),
          duration: Math.round(v.duration || 0), title: document.title,
          volume: Math.round(v.volume * 100), logged: logged};
})()"""

QR_JS = """(() => {
  const sels = ['.qrcode-img img', '.login-scan-box img', '.login-qrcode img',
                'img[src^="data:image"]', '.qrcode-img canvas', 'canvas'];
  for (const s of sels) {
    const el = document.querySelector(s);
    if (!el) continue;
    if (el.tagName === 'IMG' && el.src && el.src.indexOf('data:image') === 0)
      return {type: 'img', data: el.src};
    if (el.tagName === 'CANVAS') {
      try { return {type: 'canvas', data: el.toDataURL('image/png')}; } catch (e) {}
    }
  }
  return null;
})()"""

RECT_JS = """(() => {
  const sels = ['.qrcode-img', '.login-scan-box', '.login-qrcode', 'canvas'];
  for (const s of sels) {
    const el = document.querySelector(s);
    if (!el) continue;
    const r = el.getBoundingClientRect();
    if (r.width > 60 && r.height > 60) return {x: r.x, y: r.y, w: r.width, h: r.height};
  }
  return null;
})()"""

SEARCH_API = """(async () => {
  const q = %s, p = %d;
  const url = 'https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword='
              + encodeURIComponent(q) + '&page=' + p;
  const r = await fetch(url, {credentials: 'include'});
  const d = await r.json();
  if (d.code !== 0) return {ok: false, error: 'api ' + d.code + ' ' + (d.message || '')};
  const list = (d.data && d.data.result) || [];
  const clean = s => String(s || '').replace(/<[^>]+>/g, '')
      .replace(/&quot;/g, '"').replace(/&amp;/g, '&')
      .replace(/&lt;/g, '<').replace(/&gt;/g, '>');
  return {ok: true, items: list.map(x => ({
    bv: x.bvid,
    title: clean(x.title),
    author: clean(x.author),
    cover: x.pic ? (x.pic.indexOf('//') === 0 ? 'https:' + x.pic : x.pic) : '',
    play: x.play, duration: x.duration
  })).filter(v => v.bv)};
})()"""

SEARCH_JS = """(() => {
  return Array.from(document.querySelectorAll('.bili-video-card')).slice(0, 24).map(c => {
    const a = c.querySelector('a[href*="/video/BV"]');
    const m = a && a.href.match(/BV[0-9A-Za-z]+/);
    const tit = c.querySelector('.bili-video-card__info--tit');
    const au = c.querySelector('.bili-video-card__info--author')
            || c.querySelector('.bili-video-card__info--owner')
            || c.querySelector('.bili-video-card__info--bottom');
    const img = c.querySelector('img');
    let author = '';
    if (au) author = (au.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 40);
    return {
      bv: m ? m[0] : '',
      title: (tit && (tit.getAttribute('title') || tit.textContent || '')).trim(),
      author: author,
      cover: (img && (img.getAttribute('src') || '')) || ''
    };
  }).filter(x => x.bv);
})()"""


def _bt_device():
    """用 macOS 的 IOBluetooth 框架拿到设备对象（不需要装任何工具）。"""
    if not BT_ADDR:
        return None
    try:
        from IOBluetooth import IOBluetoothDevice
        return IOBluetoothDevice.deviceWithAddressString_(BT_ADDR)
    except Exception:
        return None


def bt_status():
    if not BT_ADDR:
        return {"connected": False, "name": BT_NAME, "addr": "", "configured": False}
    name = BT_NAME
    connected = None
    dev = _bt_device()
    if dev is not None:
        try:
            connected = bool(dev.isConnected())
            if not name:
                name = str(dev.name() or "")
        except Exception:
            connected = None
    if connected is None:
        # 退化方案：解析 system_profiler
        try:
            sp = subprocess.run(["system_profiler", "SPBluetoothDataType"],
                                capture_output=True, text=True, timeout=25).stdout
            connected = bool(name) and (name in sp) and ("Connected" in sp)
        except Exception:
            connected = False
    return {"connected": bool(connected), "name": name or BT_ADDR, "addr": BT_ADDR,
            "configured": True}


def bt_set(action: str):
    """连接 / 断开已配对设备。"""
    if not BT_ADDR:
        return {"ok": False, "error": "未配置 MB_BT_ADDR"}
    dev = _bt_device()
    if dev is None:
        return {"ok": False, "error": "IOBluetooth 不可用（请 pip install --user pyobjc-framework-IOBluetooth）"}
    try:
        if action == "connect" and not dev.isConnected():
            dev.openConnection()
        elif action == "disconnect" and dev.isConnected():
            dev.closeConnection()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    time.sleep(1.5)
    st = bt_status()
    st["ok"] = bool(st.get("connected")) if action == "connect" else True
    return st


def volume_get():
    try:
        p = subprocess.run(["/usr/bin/osascript", "-e", "output volume of (get volume settings)"],
                           capture_output=True, text=True, timeout=6)
        s = (p.stdout or "").strip()
        if s.isdigit():
            return int(s)
        if s:
            m = re.search(r"(\d+)", s)
            if m:
                return int(m.group(1))
        return None
    except Exception:
        return None


class Handler(BaseHTTPRequestHandler):
    server_version = "music-bridge/1.0"

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path == "/status":
                try:
                    t = pick_tab()
                except Exception:
                    t = None
                if not t:
                    return self._json({"ok": True, "tab": None, "bt": bt_status(),
                                       "volume": volume_get()})
                st = eval_safe(STATUS_JS) or {}
                # 音量以视频元素为准（osascript 在无 GUI 会话的后台进程里会挂住）
                st.update({"ok": True, "tab": t, "bt": bt_status()})
                if "volume" not in st:
                    st["volume"] = None
                return self._json(st)
            if u.path == "/list":
                return self._json({"ok": True, "tabs": tabs()})
            if u.path == "/search":
                try:
                    pg = max(1, min(50, int(q.get("page", ["1"])[0])))
                except Exception:
                    pg = 1
                return self._json({"ok": True, **self._search(q.get("q", [""])[0], pg)})
            if u.path == "/login":
                t = pick_tab()
                if t:
                    cdp_eval(t["id"], "location.href='https://passport.bilibili.com/login'")
                return self._json({"ok": True})
            if u.path == "/qr":
                return self._qr()
            if u.path == "/volume":
                lvl = max(0, min(100, int(q.get("level", ["50"])[0])))
                t = pick_tab()
                if t:
                    cdp_eval(t["id"],
                             f"(() => {{const v=document.querySelector('video');"
                             f"if(v){{v.volume={lvl / 100.0};}}return true;}})()")
                return self._json({"ok": True, "volume": lvl})
            if u.path == "/bt":
                return self._json({"ok": True, **bt_status()})
            return self._json({"ok": False, "error": "not found"}, 404)
        except Exception as e:
            return self._json({"ok": False, "error": str(e)}, 500)

    def do_POST(self):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path == "/play":
                t = pick_tab()
                if not t:
                    return self._json({"ok": False, "error": "no tab"}, 404)
                cdp_eval(t["id"], "document.querySelector('video') && document.querySelector('video').play()")
                return self._json({"ok": True})
            if u.path == "/pause":
                t = pick_tab()
                if not t:
                    return self._json({"ok": False, "error": "no tab"}, 404)
                cdp_eval(t["id"], "document.querySelector('video') && document.querySelector('video').pause()")
                return self._json({"ok": True})
            if u.path == "/open":
                bv = q.get("bv", [""])[0]
                if not re.fullmatch(r"BV[0-9A-Za-z]{8,14}", bv):
                    return self._json({"ok": False, "error": "bad bv"}, 400)
                t = pick_tab() or (tabs()[0] if tabs() else None)
                if not t:
                    return self._json({"ok": False, "error": "no tab"}, 404)
                import websocket
                ws = websocket.create_connection(
                    f"ws://127.0.0.1:{CDP_PORT}/devtools/page/{t['id']}", timeout=8,
                    header=["Origin: http://127.0.0.1"])
                try:
                    ws.send(json.dumps({"id": 1, "method": "Page.navigate",
                                        "params": {"url": f"https://www.bilibili.com/video/{bv}"}}))
                    ws.recv()
                finally:
                    ws.close()
                time.sleep(6)
                cdp_eval(t["id"], "(() => {const v=document.querySelector('video');"
                                  "if(v){v.muted=false;v.play();}return !!v;})()")
                return self._json({"ok": True, "bv": bv})
            if u.path == "/bt":
                act = q.get("action", ["connect"])[0]
                if act not in ("connect", "disconnect"):
                    return self._json({"ok": False, "error": "bad action"}, 400)
                return self._json(bt_set(act))
            return self._json({"ok": False, "error": "not found"}, 404)
        except Exception as e:
            return self._json({"ok": False, "error": str(e)}, 500)

    def _qr(self):
        """打开 B 站登录页，优先直接取出二维码图片，退化为裁剪截图。"""
        import websocket
        t = pick_tab()
        if not t:
            return self._json({"ok": False, "error": "no tab"}, 404)
        ws = websocket.create_connection(
            f"ws://127.0.0.1:{CDP_PORT}/devtools/page/{t['id']}", timeout=20,
            header=["Origin: http://127.0.0.1"])

        def call(i, method, params=None):
            ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))
            while True:
                m = json.loads(ws.recv())
                if m.get("id") == i:
                    return m

        try:
            call(1, "Page.navigate", {"url": "https://passport.bilibili.com/login"})
            time.sleep(6)

            r = call(2, "Runtime.evaluate",
                     {"expression": QR_JS, "returnByValue": True, "awaitPromise": True})
            val = ((r.get("result") or {}).get("result") or {}).get("value")
            if val and val.get("data"):
                data = val["data"]
                if data.startswith("data:"):
                    data = data.split(",", 1)[-1]
                return self._json({"ok": True, "png": data, "kind": "qr"})

            r2 = call(3, "Runtime.evaluate",
                      {"expression": RECT_JS, "returnByValue": True})
            rv = ((r2.get("result") or {}).get("result") or {}).get("value")
            params = {"format": "png"}
            if rv and rv.get("w", 0) > 60:
                params["clip"] = {"x": max(0, rv["x"] - 10), "y": max(0, rv["y"] - 10),
                                  "width": rv["w"] + 20, "height": rv["h"] + 20, "scale": 3}
            shot = call(4, "Page.captureScreenshot", params)
            data = ((shot.get("result") or {}).get("data") or "")
            if not data:
                return self._json({"ok": False, "error": "screenshot failed"}, 500)
            return self._json({"ok": True, "png": data, "kind": "clip"})
        except Exception as e:
            return self._json({"ok": False, "error": str(e)}, 500)
        finally:
            ws.close()

    def _search(self, qs: str, page: int = 1):
        """走 B 站搜索 API（页面内同源 fetch，自动带 cookie）。

        DOM 抓取不可靠：搜索结果页的列表区域在部分环境下不渲染，
        但接口本身返回正常，所以直接取 JSON。
        """
        qs = (qs or "").strip()[:64]
        if not qs:
            return {"query": "", "items": []}
        host = eval_safe("location.host") or ""
        if "bilibili.com" not in host:
            t = pick_tab()
            if not t:
                return {"query": qs, "items": [], "error": "no tab"}
            import websocket
            ws = websocket.create_connection(
                f"ws://127.0.0.1:{CDP_PORT}/devtools/page/{t['id']}", timeout=8,
                header=["Origin: http://127.0.0.1"])
            try:
                ws.send(json.dumps({"id": 1, "method": "Page.navigate",
                                    "params": {"url": "https://www.bilibili.com"}}))
                ws.recv()
            finally:
                ws.close()
            time.sleep(4)
        expr = SEARCH_API % (json.dumps(qs, ensure_ascii=False), page)
        res = eval_safe(expr, timeout=20) or {}
        if not res.get("ok"):
            return {"query": qs, "page": page, "items": [],
                    "error": res.get("error", "search api failed")}
        return {"query": qs, "page": page, "items": res.get("items", []),
                "href": "https://search.bilibili.com/all?keyword="
                        + urllib.parse.quote(qs) + "&page=" + str(page)}


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"music-bridge on 127.0.0.1:{PORT}, CDP {CDP_PORT}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
