#!/usr/bin/env python3
"""브라우저 점검 스크립트(`check_browser_step*.py`)가 함께 쓰는 도구. 개발 전용 — 앱은 로드하지 않는다.

이 기기에는 playwright·selenium이 없어서, 표준 라이브러리만으로 headless Chromium을
CDP(크롬 개발자 프로토콜)로 조종한다. 웹소켓 클라이언트도 직접 구현했다.
테스트 그림을 만들 때만 Pillow를 쓴다(`smoke_test_api.py`와 같은 관례).

스크린샷·임시 브라우저 프로필·테스트 그림은 **저장소 밖**(`WORK`)에 쓴다.
서버(`server.py`)는 스크립트가 빈 포트에 직접 띄우고 끝나면 끈다.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = Path(tempfile.gettempdir()) / "std04-browser-check"
KEY_INGREDIENTS = "fridge.v1.lastIngredients"
CATEGORIES = ["채소", "과일", "육류", "해산물", "유제품", "달걀", "곡물면", "양념소스", "가공식품", "음료", "기타"]
DEVNULL = subprocess.DEVNULL


# ── 결과 집계 ───────────────────────────────────────────────────────────

class Checker:
    def __init__(self):
        self.results: list[tuple[str, str, str]] = []

    def check(self, name: str, ok, evidence: str) -> None:
        status = "PASS" if ok else "FAIL"
        self.results.append((name, status, evidence))
        print(f"[{status}] {name}\n       {evidence}", flush=True)

    def skip(self, name: str, why: str) -> None:
        self.results.append((name, "SKIP", why))
        print(f"[SKIP] {name}\n       {why}", flush=True)

    def summary(self, title: str, *, show_work: bool = True) -> int:
        ran = [r for r in self.results if r[1] != "SKIP"]
        passed = sum(r[1] == "PASS" for r in ran)
        skipped = len(self.results) - len(ran)
        print("\n" + "=" * 60)
        print(f"{title} {passed}/{len(ran)} 통과" + (f" · {skipped}개 건너뜀" if skipped else "")
              + (f"  (스크린샷: {WORK})" if show_work else ""))
        for name, status, _ in self.results:
            if status == "FAIL":
                print(f"  실패: {name}")
        return 0 if passed == len(ran) else 1


# ── 서버 ────────────────────────────────────────────────────────────────

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(*, env_extra: dict | None = None, unset_key: bool = False, cwd: Path = ROOT):
    """`(프로세스, 기본 URL)`. 브라우저를 열지 않게 NO_OPEN=1로 띄운다."""
    port = free_port()
    env = dict(os.environ, NO_OPEN="1", **(env_extra or {}))
    if unset_key:
        env.pop("OPENROUTER_API_KEY", None)
    proc = subprocess.Popen([sys.executable, "server.py", str(port)], cwd=cwd, env=env, stdout=DEVNULL, stderr=DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        if proc.poll() is not None:
            raise RuntimeError(f"server.py가 시작하자마자 끝났습니다 (포트 {port})")
        try:
            urllib.request.urlopen(base + "/api/health", timeout=1)
            break
        except urllib.error.HTTPError:        # NO_KEY 등 503도 서버가 떴다는 뜻이다
            break
        except Exception:
            time.sleep(0.1)
    return proc, base


def stop(proc) -> None:
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()


# ── 최소 웹소켓 클라이언트 · CDP ─────────────────────────────────────────

class WS:
    def __init__(self, url: str):
        hostport, path = url.split("://", 1)[1].split("/", 1)
        host, port = hostport.split(":")
        self.sock = socket.create_connection((host, int(port)))
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        head, self.pending = buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise RuntimeError(head)

    def _read(self, n: int) -> bytes:
        while len(self.pending) < n:
            chunk = self.sock.recv(1 << 20)
            if not chunk:
                raise ConnectionError("websocket closed")
            self.pending += chunk
        out, self.pending = self.pending[:n], self.pending[n:]
        return out

    def _frame(self, opcode: int, data: bytes) -> None:
        hdr = bytearray([0x80 | opcode])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126); hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127); hdr += struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(bytes(hdr) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def send(self, text: str) -> None:
        self._frame(0x1, text.encode())

    def recv(self) -> str:
        msg = b""
        while True:
            b1, b2 = self._read(2)
            fin, op, n = b1 & 0x80, b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            if b2 & 0x80:
                self._read(4)
            payload = self._read(n)
            if op == 0x9:
                self._frame(0xA, payload); continue
            if op == 0x8:
                raise ConnectionError("websocket close frame")
            msg += payload
            if fin:
                return msg.decode("utf-8")


class CDP:
    def __init__(self, ws: WS):
        self.ws, self.id, self.events = ws, 0, []

    def call(self, method: str, **params):
        self.id += 1
        my = self.id
        self.ws.send(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == my:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})
            if "method" in m:
                self.events.append(m)


def chromium_binary() -> str:
    for name in (os.environ.get("CHROMIUM"), "chromium", "chromium-browser", "google-chrome"):
        if name and shutil.which(name):
            return shutil.which(name)
    raise SystemExit("Chromium을 찾지 못했습니다. CHROMIUM=<경로> 로 지정하세요.")


# 모든 페이지에 먼저 심는다: fetch 기록 + 원하는 경로의 응답을 가짜로 바꾸기(window.__fake)
FETCH_SPY = """
window.__fetches = []; window.__fake = {};
const __f = window.fetch;
window.fetch = function (url, opts) {
  const u = String(url); const rec = { url: u, faked: false, body: null };
  try { if (opts && opts.body) rec.body = JSON.parse(opts.body); } catch (e) {}
  window.__fetches.push(rec);
  for (const [part, fake] of Object.entries(window.__fake)) {
    if (u.includes(part)) {
      rec.faked = true;
      return new Promise(r => setTimeout(r, fake.delay)).then(() =>
        new Response(JSON.stringify(fake.body), { status: fake.status, headers: { 'Content-Type': 'application/json' } }));
    }
  }
  return __f.apply(this, arguments);
};"""

APP_READY = ("!window.__old && document.readyState === 'complete' && !!document.getElementById('health')"
             " && document.getElementById('health').dataset.state !== 'checking'")


class Browser:
    def __init__(self, name: str):
        self.profile = WORK / f"chrome-{name}"
        self.proc = None
        self.cdp: CDP | None = None
        self.version = ""
        self._scanned: set[str] = set()
        self._old_events: list = []
        self.bodies = 0
        self.body_hits = 0

    # 실행 · 종료
    def launch(self, fresh: bool = True) -> "Browser":
        if fresh:
            shutil.rmtree(self.profile, ignore_errors=True)
        self.profile.mkdir(parents=True, exist_ok=True)
        port = free_port()
        self.proc = subprocess.Popen(
            [chromium_binary(), "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={self.profile}",
             "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--window-size=1280,900", "about:blank"],
            stdout=DEVNULL, stderr=DEVNULL)
        targets = []
        for _ in range(150):
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1))
                if any(t["type"] == "page" for t in targets):
                    break
            except Exception:
                pass
            time.sleep(0.2)
        page = next((t for t in targets if t["type"] == "page"), None)
        if not page:
            raise RuntimeError("Chromium 페이지에 연결하지 못했습니다")
        if self.cdp:
            self._old_events += self.cdp.events
        self.cdp = CDP(WS(page["webSocketDebuggerUrl"]))
        self.version = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version"))["Browser"]
        for dom in ("Page", "Runtime", "Network", "Log", "DOM"):
            self.cdp.call(f"{dom}.enable")
        self.viewport(1280)
        self.cdp.call("Page.addScriptToEvaluateOnNewDocument", source=FETCH_SPY)
        return self

    def close(self, graceful: bool = False) -> None:
        if graceful and self.cdp:
            try:
                self.scan_bodies()
            except Exception:
                pass
            try:
                self.cdp.call("Browser.close")
            except Exception:
                pass
            try:
                self.proc.wait(15)
                return
            except subprocess.TimeoutExpired:
                pass
        stop(self.proc)

    def all_events(self) -> list:
        return self._old_events + (self.cdp.events if self.cdp else [])

    # 페이지 조작
    def js(self, expr: str):
        r = self.cdp.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]
            raise RuntimeError(f"JS 오류: {d.get('text')} {d.get('exception', {}).get('description')}")
        return r["result"].get("value")

    def wait(self, expr: str, timeout: float = 10.0, step: float = 0.25):
        end = time.time() + timeout
        while time.time() < end:
            v = self.js(expr)
            if v:
                return v
            time.sleep(step)
        return self.js(expr)

    def scan_bodies(self) -> None:
        """받은 응답 본문을 전부 읽어 sk-or-v1-을 센다. 페이지가 바뀌기 전에 불러야 한다."""
        for e in list(self.cdp.events):
            if e["method"] != "Network.loadingFinished":
                continue
            rid = e["params"]["requestId"]
            if rid in self._scanned:
                continue
            self._scanned.add(rid)
            try:
                r = self.cdp.call("Network.getResponseBody", requestId=rid)
            except RuntimeError:
                continue
            body = base64.b64decode(r["body"]).decode("latin-1") if r.get("base64Encoded") else r["body"]
            self.bodies += 1
            self.body_hits += body.count("sk-or-v1-")

    def navigate(self, url: str):
        self.scan_bodies()
        try:
            self.js("window.__old = true")
        except RuntimeError:
            pass
        self.cdp.call("Page.navigate", url=url)
        return self.wait(APP_READY, 15)

    def reload(self):
        self.scan_bodies()
        self.js("window.__old = true")
        self.cdp.call("Page.reload", ignoreCache=True)
        return self.wait(APP_READY, 15)

    def viewport(self, width: int, height: int = 900) -> None:
        self.cdp.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)

    def set_file(self, path: Path) -> None:
        root = self.cdp.call("DOM.getDocument", depth=0)["root"]["nodeId"]
        nid = self.cdp.call("DOM.querySelector", nodeId=root, selector="#file")["nodeId"]
        self.cdp.call("DOM.setFileInputFiles", files=[str(path)], nodeId=nid)

    def shot(self, name: str) -> Path:
        data = self.cdp.call("Page.captureScreenshot", format="png", captureBeyondViewport=True)["data"]
        p = WORK / name
        p.write_bytes(base64.b64decode(data))
        return p

    def ls(self, key: str):
        """localStorage 값을 JSON으로. 깨졌으면 '__broken__'."""
        return self.js(f"(() => {{ try {{ return JSON.parse(localStorage.getItem({json.dumps(key)})); }} catch (e) {{ return '__broken__'; }} }})()")

    def raw(self, key: str):
        return self.js(f"localStorage.getItem({json.dumps(key)})")

    def set_list(self, obj) -> None:
        self.js(f"localStorage.setItem({json.dumps(KEY_INGREDIENTS)}, {json.dumps(json.dumps(obj, ensure_ascii=False))}); true")

    def fake(self, part: str, body, *, status: int = 200, delay: int = 300) -> None:
        self.js(f"window.__fake[{json.dumps(part)}] = {{ status: {status}, delay: {delay}, body: {json.dumps(body, ensure_ascii=False)} }}; true")

    def unfake(self) -> None:
        self.js("window.__fake = {}; true")

    def fetch_count(self, part: str) -> int:
        return self.js(f"window.__fetches.filter(f => f.url.includes({json.dumps(part)})).length")

    def responses(self, part: str) -> list:
        """실제 네트워크로 받은 JSON 응답들(가짜 응답은 네트워크에 잡히지 않는다)."""
        out = []
        for e in list(self.cdp.events):
            if e["method"] == "Network.responseReceived" and part in e["params"]["response"]["url"]:
                for _ in range(20):
                    try:
                        out.append(json.loads(self.cdp.call("Network.getResponseBody", requestId=e["params"]["requestId"])["body"]))
                        break
                    except RuntimeError:
                        self.js("1"); time.sleep(0.2)
        return out

    def pump(self) -> None:
        """렌더러가 멈춰 있어도(confirm 대화상자) 답하는 호출로 이벤트를 받아 온다."""
        for method in ("Browser.getVersion", "Page.getNavigationHistory"):
            try:
                self.cdp.call(method)
                return
            except RuntimeError:
                continue

    def click_with_dialog(self, click_js: str, accept: bool, timeout: float = 5):
        """confirm()을 띄우는 클릭. 대화상자 문구를 돌려주고, 안 뜨면 None."""
        n0 = len(self.cdp.events)
        self.js(f"setTimeout(() => {{ {click_js} }}, 0); true")
        end = time.time() + timeout
        while time.time() < end:
            self.pump()
            ev = [e for e in self.cdp.events[n0:] if e["method"] == "Page.javascriptDialogOpening"]
            if ev:
                self.cdp.call("Page.handleJavaScriptDialog", accept=accept)
                return ev[-1]["params"]["message"]
            time.sleep(0.1)
        return None

    def exceptions_since(self, n0: int) -> list[str]:
        return [e["params"]["exceptionDetails"].get("exception", {}).get("description", e["params"]["exceptionDetails"].get("text"))
                for e in self.cdp.events[n0:] if e["method"] == "Runtime.exceptionThrown"]

    def security(self):
        """`(통과, 근거, 콘솔 메시지 요약)` — 응답 본문·네트워크 이벤트·콘솔 어디에도 sk-or-v1-이 없는가."""
        try:
            self.scan_bodies()
        except Exception:
            pass
        evs = self.all_events()
        net = sum(json.dumps(e, ensure_ascii=False).count("sk-or-v1-") for e in evs if e["method"].startswith("Network."))
        console = [e for e in evs if e["method"] in ("Runtime.consoleAPICalled", "Log.entryAdded")]
        con = sum(json.dumps(e, ensure_ascii=False).count("sk-or-v1-") for e in console)
        texts = sorted({(e["params"].get("entry", {}).get("text", "")[:80] + " " + e["params"].get("entry", {}).get("url", "")[-30:]).strip()
                        for e in console})
        evidence = (f"응답 본문 {self.bodies}개 중 {self.body_hits}건, 네트워크 이벤트 {net}건, "
                    f"콘솔 {len(console)}건 중 {con}건")
        return self.body_hits == 0 and net == 0 and con == 0, evidence, texts


# ── 앱 조작 도우미 ──────────────────────────────────────────────────────

def click(b: Browser, element_id: str, times: int = 1) -> None:
    b.js(f"(() => {{ const el = document.getElementById({json.dumps(element_id)}); for (let i = 0; i < {times}; i++) el.click(); return true; }})()")


def add_item(b: Browser, name: str, qty: str, cat: str) -> None:
    b.js(f"""(() => {{ const f = document.getElementById('add-form');
        f.elements.name.value = {json.dumps(name)}; f.elements.quantity.value = {json.dumps(qty)};
        f.elements.category.value = {json.dumps(cat)}; f.requestSubmit(); return true; }})()""")


def overflow(b: Browser, selector: str = "main *, header *") -> dict:
    return b.js(f"""(() => {{
        const over = Array.from(document.querySelectorAll({json.dumps(selector)}))
          .filter(e => e.offsetParent !== null && e.getBoundingClientRect().right > window.innerWidth + 0.5)
          .map(e => e.tagName + (e.id ? '#' + e.id : '') + '.' + e.className).slice(0, 5);
        return {{ inner: window.innerWidth, scroll: document.documentElement.scrollWidth, over }}; }})()""")


def secs(text) -> int | None:
    try:
        return int((text or "").replace("초째", ""))
    except ValueError:
        return None


CARDS = """Array.from(document.querySelectorAll('#recipes .recipe')).map(c => ({
  id: c.dataset.id, title: c.querySelector('h3').textContent, badge: !!c.querySelector('.badge-now'),
  have: Array.from(c.querySelectorAll('.chips.have .chip:not(.none)')).map(x => x.textContent),
  missing: Array.from(c.querySelectorAll('.chips.missing .chip:not(.none)')).map(x => x.textContent),
  steps: c.querySelectorAll('.recipe-steps li').length,
  meta: (c.querySelector('.recipe-meta') || {}).textContent || '' }))"""

STATUS_DONE = "document.getElementById('status').dataset.state !== 'loading' && document.getElementById('status').dataset.state"
RECIPE_DONE = "document.getElementById('recipe-status').dataset.state !== 'loading' && document.getElementById('recipe-status').dataset.state"


def recipe(rid: str, title: str, *, minutes: int = 10, uses=("달걀",), missing=(), steps=("하나", "둘"), difficulty="쉬움",
           servings: int = 2, summary: str = "요약", tips: str = "팁") -> dict:
    return {"id": rid, "title": title, "summary": summary, "time_minutes": minutes, "difficulty": difficulty, "servings": servings,
            "uses": list(uses), "missing": list(missing), "steps": list(steps), "tips": tips}


def recipes_body(*recipes: dict, elapsed_ms: int = 5) -> dict:
    return {"ok": True, "meta": {"elapsed_ms": elapsed_ms}, "data": {"recipes": list(recipes)}}


BROKEN_LIST = {
    "items": [
        {"name": "달modification달걀", "category": "달걀", "quantity": "6개", "confidence": "low", "needs_review": True, "source": "vision"},
        {"name": "우유", "category": "유제품", "quantity": "1L", "confidence": "high", "needs_review": False, "source": "vision"},
        {"name": "양배추", "category": "채소", "quantity": "1통", "confidence": "medium", "needs_review": False, "source": "vision"},
    ],
    "unsure": ["문 안쪽 초록색 병 — 라벨이 가려 무엇인지 모르겠음"],
    "analyzed_at": "2026-09-14T10:00:00+09:00",
}


def make_assets() -> dict[str, Path]:
    """PDF 한 개, EXIF 회전이 붙은 4000px 세로 사진, 식재료 없는 풍경 그림을 WORK에 만든다."""
    from PIL import Image, ImageDraw, ImageOps

    WORK.mkdir(parents=True, exist_ok=True)
    pdf = WORK / "not_image.pdf"
    pdf.write_bytes(b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")

    # 똑바로 선 3000x4000(위 빨강·아래 파랑)을 눕혀 4000x3000으로 저장하고 EXIF Orientation=6을 붙인다.
    upright = Image.new("RGB", (3000, 4000), (128, 128, 128))
    d = ImageDraw.Draw(upright)
    d.rectangle((0, 0, 3000, 500), fill=(220, 20, 20))
    d.rectangle((0, 3500, 3000, 4000), fill=(20, 40, 220))
    exif = Image.Exif()
    exif[274] = 6
    exif_path = WORK / "portrait_4000_exif6.jpg"
    upright.transpose(Image.ROTATE_90).save(exif_path, "JPEG", quality=80, exif=exif)
    back = ImageOps.exif_transpose(Image.open(exif_path))
    assert back.size == (3000, 4000) and back.getpixel((1500, 50))[0] > 180, "EXIF 테스트 그림 생성 오류"

    landscape = WORK / "landscape.jpg"
    im = Image.new("RGB", (768, 512))
    d = ImageDraw.Draw(im)
    for y in range(300):
        d.line([(0, y), (768, y)], fill=(110 + y // 6, 170 + y // 10, 235))
    d.polygon([(0, 300), (200, 170), (420, 300)], fill=(90, 110, 130))
    d.polygon([(300, 300), (560, 140), (768, 300)], fill=(70, 95, 120))
    d.rectangle((0, 300, 768, 512), fill=(80, 150, 70))
    d.ellipse((620, 40, 700, 120), fill=(255, 230, 120))
    im.save(landscape, "JPEG", quality=85)
    return {"pdf": pdf, "exif": exif_path, "landscape": landscape}
