#!/usr/bin/env python3
"""한눈 PDF 요약 — 프런트엔드 화면 점검. 개발 전용(앱은 이 파일을 로드하지 않는다). 모델 호출 0회.

playwright·selenium이 없어 표준 라이브러리로 headless Chromium을 CDP로 조종한다.
tools/mock_server.py를 127.0.0.1:8031에 띄워 SPEC §7의 모든 상태와 §7.4의 모든 오류 문구를 재현하고,
1280·480·360·320px 스크린샷을 저장소 밖 폴더에 남긴다. 끝나면 가짜 서버와 브라우저를 끈다.

    python3 -B tools/check_frontend.py --out 저장소_밖_폴더

업로드할 파일은 --out 아래 files/에 만든다(내용은 '%PDF-1.4'로 시작하는 아무 바이트 — 가짜 서버는
파일 이름으로 시나리오를 고른다. 이름 규칙은 tools/mock_server.py 머리 주석).
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
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else Path(tempfile.gettempdir()) / "pdf-summary-frontend-check"
FILES = OUT / "files"
PORT = 8031
BASE = f"http://127.0.0.1:{PORT}/"
DEVNULL = subprocess.DEVNULL
KST = timezone(timedelta(hours=9))


def stop(proc) -> None:
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()


def port_busy(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


# ── 최소 웹소켓 · CDP ─────────────────────────────────────

class WS:
    def __init__(self, url: str):
        hostport, path = url.split("://", 1)[1].split("/", 1)
        host, port = hostport.split(":")
        self.sock = socket.create_connection((host, int(port)))
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
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


class Browser:
    def __init__(self):
        self.profile = OUT / "chrome-profile"
        shutil.rmtree(self.profile, ignore_errors=True)
        self.profile.mkdir(parents=True, exist_ok=True)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
        self.proc = subprocess.Popen(
            [shutil.which("chromium") or shutil.which("chromium-browser"), "--headless=new",
             f"--remote-debugging-port={port}", f"--user-data-dir={self.profile}", "--no-first-run",
             "--no-default-browser-check", "--disable-gpu", "--window-size=1280,900", "about:blank"],
            stdout=DEVNULL, stderr=DEVNULL)
        page = None
        for _ in range(150):
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1))
                page = next((t for t in targets if t["type"] == "page"), None)
                if page:
                    break
            except Exception:
                pass
            time.sleep(0.2)
        if not page:
            raise RuntimeError("Chromium에 연결하지 못했습니다")
        self.ws = WS(page["webSocketDebuggerUrl"])
        self.id = 0
        self.events: list = []
        for d in ("Page", "Runtime", "Log", "Network", "DOM"):
            self.call(f"{d}.enable")

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

    def js(self, expr: str):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True, userGesture=False)
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]
            raise RuntimeError(f"JS 오류: {d.get('text')} {d.get('exception', {}).get('description')}")
        return r["result"].get("value")

    def wait(self, expr: str, timeout: float = 10.0):
        end = time.time() + timeout
        while time.time() < end:
            try:
                v = self.js(expr)
            except RuntimeError:
                v = None
            if v:
                return v
            time.sleep(0.1)
        return self.js(expr)

    def goto(self, url: str, ready: str = "document.readyState === 'complete'") -> None:
        try:
            self.js("window.__old = true")
        except RuntimeError:
            pass
        self.call("Page.navigate", url=url)
        self.wait(f"!window.__old && {ready}", 15)

    def viewport(self, width: int, height: int = 900) -> None:
        self.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)
        time.sleep(0.15)

    def shot(self, name: str, full: bool = True) -> Path:
        data = self.call("Page.captureScreenshot", format="png", captureBeyondViewport=full)["data"]
        p = OUT / f"{name}.png"
        p.write_bytes(base64.b64decode(data))
        return p

    def key(self, key: str, code: str, vk: int, text: str = "", modifiers: int = 0) -> None:
        for t in ("keyDown", "keyUp"):
            self.call("Input.dispatchKeyEvent", type=t, key=key, code=code, windowsVirtualKeyCode=vk, modifiers=modifiers,
                      **({"text": text} if text and t == "keyDown" else {}))

    def tab(self, shift: bool = False) -> str:
        self.key("Tab", "Tab", 9, modifiers=8 if shift else 0)
        return self.js("document.activeElement.id || document.activeElement.dataset.action || document.activeElement.tagName")

    def center(self, selector: str):
        return self.js(f"""(() => {{ const e = document.querySelector({json.dumps(selector)}); if (!e) return null;
            e.scrollIntoView({{block: 'center'}}); const r = e.getBoundingClientRect();
            return [r.left + r.width / 2, r.top + r.height / 2]; }})()""")

    def click(self, selector: str) -> None:
        """진짜 마우스 클릭(사용자 동작으로 인정되어 파일 고르기 창도 열린다)."""
        xy = self.center(selector)
        if not xy:
            raise RuntimeError(f"요소 없음: {selector}")
        for t in ("mouseMoved", "mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", type=t, x=xy[0], y=xy[1], button="left", clickCount=1)

    def set_files(self, paths: list[str]) -> None:
        root = self.call("DOM.getDocument", depth=1)["root"]["nodeId"]
        node = self.call("DOM.querySelector", nodeId=root, selector="#file-input")["nodeId"]
        self.call("DOM.setFileInputFiles", files=paths, nodeId=node)

    def chooser_events(self) -> list:
        self.js("1")   # 쌓인 이벤트를 받아 오게 한 번 부른다
        return [e for e in self.events if e["method"] == "Page.fileChooserOpened"]

    def errors(self) -> list[str]:
        out = []
        for e in self.events:
            if e["method"] == "Runtime.exceptionThrown":
                out.append(e["params"]["exceptionDetails"].get("exception", {}).get("description", "exception"))
            elif e["method"] == "Runtime.consoleAPICalled" and e["params"]["type"] in ("error", "warning"):
                out.append(f"console.{e['params']['type']}: " + str(e["params"]["args"])[:200])
            elif e["method"] == "Log.entryAdded" and e["params"]["entry"]["level"] in ("error", "warning") \
                    and e["params"]["entry"].get("source") != "network":
                out.append("log: " + e["params"]["entry"]["text"][:200])
        return out

    def network_errors(self) -> list[str]:
        return [e["params"]["entry"]["text"] for e in self.events
                if e["method"] == "Log.entryAdded" and e["params"]["entry"]["level"] == "error"
                and e["params"]["entry"].get("source") == "network"]

    def close(self) -> None:
        try:
            self.call("Browser.close")
            self.proc.wait(10)
        except Exception:
            pass
        stop(self.proc)


# ── 가짜 서버 제어 ──────────────────────────────────────

def mock(path: str, method: str = "GET"):
    req = urllib.request.Request(BASE.rstrip("/") + path, method=method, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def mlog(path: str | None = None) -> list:
    return [e for e in mock("/__mock/log") if path is None or e["path"] == path]


# ── 점검 ────────────────────────────────────────────────

results: list[tuple[str, bool, str]] = []


def check(name: str, ok, evidence="") -> None:
    results.append((name, bool(ok), str(evidence)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n       {evidence}" if evidence and not ok else ""), flush=True)


SPY = """
window.__fetches = [];
const __f = window.fetch;
window.fetch = function (u) { window.__fetches.push(String(u)); return __f.apply(this, arguments); };
"""

V = {  # 보이는 구역
    "upload": "!document.getElementById('upload').hidden",
    "doc": "!document.getElementById('doc').hidden",
    "progress": "!document.getElementById('progress').hidden",
    "error": "!document.getElementById('error').hidden",
    "result": "!document.getElementById('result').hidden",
}
READY = "document.documentElement.classList.contains('app-ready')"

# §7.4 문구에서 코드마다 꼭 들어가야 할 부분
EXPECT = {
    "NOT_PDF": "PDF 파일만 요약할 수 있어요",
    "EMPTY_FILE": "빈 파일이에요",
    "MULTIPLE_FILES": "한 번에 파일 하나만",
    "FILE_TOO_LARGE": "파일이 너무 커요(20MB까지)",
    "TOO_LONG": "문서가 너무 길어요(300쪽까지)",
    "ENCRYPTED": "암호가 걸린 PDF예요",
    "BROKEN_PDF": "PDF를 열 수 없어요",
    "NO_TEXT": "글자를 찾지 못했어요",
    "EXTRACT_TIMEOUT": "PDF를 읽는 데 너무 오래 걸려",
    "TOOL_MISSING": "sudo apt install poppler-utils",
    "NO_KEY": "python3 -B config.py",
    "BUSY": "다른 요약이 진행 중이에요",
    "RATE_LIMIT": "1분쯤 뒤에",
    "TIMEOUT": "요약이 너무 오래 걸려",
    "CLIENT_TIMEOUT": "요약이 너무 오래 걸려",
    "UPSTREAM": "AI가 요약을 제대로 돌려주지 못했어요",
    "BAD_OUTPUT": "AI가 요약을 제대로 돌려주지 못했어요",
    "NETWORK": "./run.sh",
    "BAD_REQUEST": "알 수 없는 문제가 생겼어요",
    "INTERNAL": "알 수 없는 문제가 생겼어요",
    "FORBIDDEN_HOST": "알 수 없는 문제가 생겼어요",
}


def text_of(b: Browser, id_: str) -> str:
    return " ".join((b.js(f"document.getElementById('{id_}').textContent") or "").split())


def make_files() -> dict[str, str]:
    shutil.rmtree(FILES, ignore_errors=True)
    FILES.mkdir(parents=True)
    pdf = b"%PDF-1.4\n" + b"mock body " * 50
    names = ["ko.pdf", "한글 이름 파일.pdf", "mixed.pdf", "notitle.pdf", "long-sumslow-2.pdf", "mid-sumslow-2.pdf",
             "xss.pdf", "longword.pdf", "once-UPSTREAM.pdf", "sumslow-2.pdf", "sumslow-3.pdf", "slow-2.pdf", "slow-3.pdf",
             "nojson.pdf", "badshape.pdf", "sumnojson.pdf", "sumbadshape.pdf",
             "sum-DAILY_LIMIT.pdf", "sum-DAILY_LIMIT-noreset.pdf", "sum-DAILY_LIMIT-reset3d.pdf",
             "UPPER.PDF"]
    names += [f"err-{c}.pdf" for c in ["NOT_PDF", "ENCRYPTED", "BROKEN_PDF", "NO_TEXT", "TOO_LONG", "EXTRACT_TIMEOUT",
                                       "TOOL_MISSING", "BAD_REQUEST", "INTERNAL", "FORBIDDEN_HOST"]]
    names += [f"sum-{c}.pdf" for c in ["NO_KEY", "BUSY", "RATE_LIMIT", "TIMEOUT", "UPSTREAM", "BAD_OUTPUT", "TOO_LONG",
                                       "BAD_REQUEST", "INTERNAL"]]
    out = {}
    for n in names:
        (FILES / n).write_bytes(pdf)
        out[n] = str(FILES / n)
    (FILES / "fake.pdf").write_bytes(b"just text, not a pdf")
    out["fake.pdf"] = str(FILES / "fake.pdf")
    (FILES / "zero.pdf").write_bytes(b"")
    out["zero.pdf"] = str(FILES / "zero.pdf")
    with open(FILES / "big.pdf", "wb") as f:      # 21,000,009바이트(희소 파일 — 디스크는 거의 안 씀)
        f.write(b"%PDF-1.4\n")
        f.truncate(21_000_009)
    out["big.pdf"] = str(FILES / "big.pdf")
    (FILES / "note.txt").write_text("글")
    out["note.txt"] = str(FILES / "note.txt")
    return out


def upload(b: Browser, path: str, wait_for: str | None = "settled", timeout: float = 10) -> None:
    b.set_files([path])
    if wait_for == "settled":
        b.wait(f"!({V['progress']}) && ({V['doc']} || {V['error']})", timeout)


def summarize(b: Browser, timeout: float = 10) -> None:
    b.click("#summarize-button")
    b.wait(f"!({V['progress']}) && ({V['result']} || {V['error']})", timeout)


def fresh(b: Browser) -> None:
    """처음 화면으로(새로고침)."""
    b.goto(BASE, READY)


def expected_reset(days: int) -> str:
    """가짜 서버가 주는 reset_at(다음 00:00 UTC + days-1일)을 app.js와 같은 규칙으로 KST 문구로."""
    now = datetime.now(timezone.utc)
    t = (now + timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(KST)
    d = (t.date() - now.astimezone(KST).date()).days
    day = {0: "오늘", 1: "내일", 2: "모레"}.get(d, f"{t.month}월 {t.day}일")
    return f"{day} 오전 9시"


def overflow(b: Browser) -> dict:
    return b.js("""(() => {
      const de = document.documentElement;
      const wide = [...document.querySelectorAll('body *')].filter(e => {
        const r = e.getBoundingClientRect(); return r.width && (r.right > de.clientWidth + 1 || r.left < -1) && !e.closest('.visually-hidden, .copy-buffer, .bar');  // .bar는 overflow:hidden으로 잘림
      }).map(e => e.tagName + '#' + e.id + '.' + e.className).slice(0, 5);
      return { scroll: de.scrollWidth, client: de.clientWidth, wide };
    })()""")


def small_targets(b: Browser) -> list:
    return b.js("""[...document.querySelectorAll('button, a[href], input:not([type=file]), summary')].filter(e => {
        if (e.closest('.visually-hidden')) return false;
        const r = e.getBoundingClientRect(); if (!r.width && !r.height) return false;
        return r.width < 44 || r.height < 44;
      }).map(e => `${e.tagName} "${(e.textContent||'').trim().slice(0,20)}" ${Math.round(e.getBoundingClientRect().width)}x${Math.round(e.getBoundingClientRect().height)}`)""")


CONTRAST_JS = r"""(() => {
  const parse = c => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const lum = ({r,g,b}) => { const f = v => { v /= 255; return v <= .03928 ? v/12.92 : ((v+.055)/1.055) ** 2.4; }; return .2126*f(r) + .7152*f(g) + .0722*f(b); };
  const bgOf = el => { for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > .5) return c; } return { r: 255, g: 255, b: 255, a: 1 }; };
  const bad = []; let n = 0;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  while (walker.nextNode()) {
    const t = walker.currentNode; const el = t.parentElement;
    if (!t.textContent.trim() || seen.has(el)) continue; seen.add(el);
    if (el.closest('[hidden], .visually-hidden, template')) continue;
    const r = el.getBoundingClientRect(); if (!r.width) continue;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    const fg = parse(cs.color), bg = bgOf(el);
    const L1 = lum(fg), L2 = lum(bg); const ratio = (Math.max(L1,L2)+.05)/(Math.min(L1,L2)+.05);
    const size = parseFloat(cs.fontSize), bold = +cs.fontWeight >= 700;
    const need = (size >= 24 || (bold && size >= 18.66)) ? 3 : 4.5;
    n++;
    if (ratio < need) bad.push(`${el.tagName}.${el.className} "${t.textContent.trim().slice(0,15)}" ${ratio.toFixed(2)}`);
  }
  return { bad, checked: n };
})()"""


def layout_checks(b: Browser, label: str, widths=(1280, 480, 360, 320)) -> None:
    for w in widths:
        b.viewport(w, 900)
        ov = overflow(b)
        st = small_targets(b)
        check(f"{label} {w}px: 가로 넘침 없음·터치 대상 44px 이상", ov["scroll"] <= ov["client"] and not ov["wide"] and not st,
              {"overflow": ov, "small": st})
        b.shot(f"{label}-{w}")
    b.viewport(1280)


def main() -> int:
    if port_busy(PORT):
        print(f"포트 {PORT}이 이미 쓰이고 있습니다. 가짜 서버를 띄울 수 없어 멈춥니다.")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    F = make_files()
    server = subprocess.Popen([sys.executable, "-B", str(ROOT / "tools" / "mock_server.py"), str(PORT)], stdout=DEVNULL, stderr=DEVNULL)
    b = None
    try:
        for _ in range(50):
            if port_busy(PORT):
                break
            time.sleep(0.1)
        b = Browser()
        b.call("Page.addScriptToEvaluateOnNewDocument", source=SPY)
        b.call("Page.setInterceptFileChooserDialog", enabled=True)
        b.call("Emulation.setTimezoneOverride", timezoneId="Asia/Seoul")
        b.viewport(1280)
        mock("/__mock/reset", "POST")
        fresh(b)

        # ── 1. 첫 화면 ──
        check("첫 화면: 앱 준비, file:// 안내 숨김, 업로드 영역·외부 전송 안내 보임",
              b.js(READY) and b.js("getComputedStyle(document.getElementById('boot-notice')).display") == "none"
              and b.js(V["upload"]) and not b.js(V["doc"]) and not b.js(V["error"]) and not b.js(V["result"])
              and "OpenRouter" in text_of(b, "privacy") and "민감한 문서" in text_of(b, "privacy"))
        check("첫 화면: 업로드 안내 문구·상한", "PDF를 여기에 끌어다 놓거나 눌러서 고르세요" in text_of(b, "drop-zone")
              and "20MB · 300쪽까지" in text_of(b, "drop-zone"), text_of(b, "drop-zone"))
        check("첫 화면: 서버 정상이면 미리 안내 없음", b.wait("window.__fetches.includes('/api/health')", 3)
              and b.js("document.getElementById('health-notices').children.length") == 0)
        check("<html lang=ko>·h1 앱 이름", b.js("document.documentElement.lang") == "ko"
              and b.js("document.querySelector('h1').textContent") == "한눈 PDF 요약")
        check("파일 입력: accept=application/pdf,.pdf", b.js("document.getElementById('file-input').accept") == "application/pdf,.pdf")
        b.shot("01-ready-1280")

        # ── 2. 키보드만으로: Tab → Enter로 고르기 창 → 파일 → Enter로 요약 → 결과 ──
        n0 = len(b.chooser_events())
        first = b.tab()
        b.wait("document.activeElement.id === 'pick-button'", 2)
        outline = b.js("getComputedStyle(document.activeElement).outlineStyle + ' ' + getComputedStyle(document.activeElement).outlineWidth")
        check("키보드: 첫 Tab이 [PDF 파일 고르기], 포커스 테두리 보임", first == "pick-button" and outline.startswith("solid") and outline.endswith("3px"),
              f"{first} {outline}")
        b.key("Enter", "Enter", 13, "\r")
        b.wait(f"1", 1)
        time.sleep(0.3)
        ev = b.chooser_events()
        check("키보드: Enter로 파일 고르기 창이 열림", len(ev) == n0 + 1, len(ev))
        if len(ev) > n0:
            b.call("DOM.setFileInputFiles", files=[F["ko.pdf"]], backendNodeId=ev[-1]["params"]["backendNodeId"])
        b.wait(V["doc"], 5)
        check("키보드: 읽기 완료 뒤 포커스가 [요약하기]로", b.js("document.activeElement.id") == "summarize-button")
        b.key(" ", "Space", 32, " ")
        b.wait(V["result"], 6)
        check("키보드: Space로 요약 → 결과, 포커스는 '한 줄 요약' 제목",
              b.js(V["result"]) and b.js("document.activeElement.id") == "result-one-title"
              and b.js("document.activeElement.textContent") == "한 줄 요약")
        nxt = b.tab()
        check("키보드: 결과에서 Tab → [결과 복사]", nxt == "copy-button", nxt)
        b.key(" ", "Space", 32, " ")
        nxt = b.tab()
        b.key("Enter", "Enter", 13, "\r")
        time.sleep(0.3)
        check("키보드: [다른 파일 고르기] → 처음 화면 + 고르기 창 열림", nxt == "another" and b.js(V["upload"]) and not b.js(V["result"])
              and len(b.chooser_events()) == n0 + 2, nxt)

        # Escape 대신: 고르기 창을 닫은 상태(파일 안 고름)로 남음 — 처음 화면 유지
        check("고르기 창을 그냥 닫으면 처음 화면 그대로", b.js(V["upload"]) and not b.js(V["error"]))

        # ── 3. 마우스로 영역 아무 곳 클릭 → 고르기 창 ──
        n1 = len(b.chooser_events())
        b.click("#drop-title")
        time.sleep(0.3)
        check("마우스: 업로드 영역 글자를 눌러도 고르기 창이 열림(한 번만)", len(b.chooser_events()) == n1 + 1, len(b.chooser_events()) - n1)

        # ── 4. 파일 선택 업로드 · 문서 정보 · 요청 모양 ──
        mock("/__mock/reset", "POST")
        fresh(b)
        upload(b, F["한글 이름 파일.pdf"])
        lg = mlog("/api/extract")
        check("파일 선택 업로드: 한글 파일 이름이 X-Filename으로 그대로 전달", lg and lg[-1]["file_name"] == "한글 이름 파일.pdf"
              and lg[-1]["bytes"] == os.path.getsize(F["한글 이름 파일.pdf"]), lg[-1:] if lg else lg)
        check("읽기 완료: 문서 정보 카드(제목·쪽수·글자 수·크기)",
              text_of(b, "doc-title") == "2026년 지역 도서관 운영 보고서" and text_of(b, "doc-pages") == "5쪽"
              and text_of(b, "doc-chars") == "3,214자" and text_of(b, "doc-size") == "509바이트"
              and "한글 이름 파일.pdf" in text_of(b, "doc-file"),
              [text_of(b, i) for i in ("doc-title", "doc-pages", "doc-chars", "doc-size", "doc-file")])
        check("읽기 완료: [요약하기]·[다른 파일] 보임, 업로드 영역 숨김, 요약 요청은 아직 0회",
              b.js("!document.getElementById('summarize-button').disabled") and not b.js(V["upload"])
              and not mlog("/api/summarize"))
        check("읽기 완료: 잘림·빈 쪽 안내 없음", b.js("document.getElementById('doc-truncated').hidden && document.getElementById('doc-empty-pages').hidden"))
        check("읽기 완료: 외부 전송 안내가 [요약하기] 바로 아래", b.js("""(() => { const a = document.getElementById('doc-actions').getBoundingClientRect();
              const p = document.getElementById('privacy').getBoundingClientRect(); return p.top > a.bottom && p.top - a.bottom < 80; })()"""))
        b.shot("02-extracted-1280")
        b.viewport(480); b.shot("02-extracted-480"); b.viewport(1280)

        # ── 5. 결과 · 복사 ──
        b.js("""window.__copied = null; navigator.clipboard.writeText = (t) => { window.__copied = t; return Promise.resolve(); };""")
        summarize(b)
        check("결과: 한 줄 요약·요약·핵심 요점(목록 4개)·모델 정보",
              "12% 늘어" in text_of(b, "result-one") and "검토하고 있습니다" in text_of(b, "result-overview")
              and b.js("document.querySelectorAll('#result-points > li').length") == 4
              and b.js("document.getElementById('result-points').tagName") == "UL"
              and text_of(b, "result-meta") == "모델 mock/example-model:free · 호출 1회 · 0.3초", text_of(b, "result-meta"))
        check("결과: 제목 구조(h1 → h2 한 줄 요약·요약·핵심 요점)", b.js("[...document.querySelectorAll('#result h2')].map(h => h.textContent)") == ["한 줄 요약", "요약", "핵심 요점"])
        check("결과: 문서 정보 카드는 남고 [요약하기]는 숨김", b.js(V["doc"]) and b.js("document.getElementById('doc-actions').hidden"))
        check("결과: aria-live로 완료 알림", "요약이 끝났어요" in b.wait("document.getElementById('announcer').textContent", 2)
              and b.js("document.getElementById('announcer').getAttribute('aria-live')") == "polite")
        b.click("#copy-button")
        b.wait("window.__copied", 2)
        expect = "\n".join(["2026년 지역 도서관 운영 보고서",
                            "한 줄 요약: 지역 도서관 이용자가 12% 늘어 저녁 열람실 연장과 주말 어린이 프로그램을 검토한다는 보고서입니다.",
                            "", "요약",
                            "지난해보다 도서관 이용자가 12% 늘었고, 특히 평일 저녁 이용이 크게 늘었습니다. 도서관은 저녁 열람실 운영을 한 시간 연장하는 방안을 검토하고 있습니다. 어린이 독서 프로그램은 주말로 옮겨 가족 단위 참여를 늘리려 합니다.",
                            "", "핵심 요점", "- 이용자 수가 지난해보다 12% 증가", "- 평일 저녁 시간대 이용이 크게 늘어남",
                            "- 저녁 열람실 운영 한 시간 연장 검토", "- 어린이 독서 프로그램을 주말로 이동 검토"])
        check("복사: §7.2 형식과 정확히 일치", b.js("window.__copied") == expect, repr(b.js("window.__copied"))[:300])
        check("복사: '복사했어요' 표시 후 2초 뒤 사라짐", text_of(b, "copy-status") == "복사했어요"
              and b.wait("document.getElementById('copy-status').textContent === ''", 3))
        b.shot("03-result-1280")
        b.viewport(480); b.shot("03-result-480"); b.viewport(1280)
        # 대체 경로(execCommand)
        b.js("""navigator.clipboard.writeText = () => Promise.reject(new Error('denied'));
                window.__copied = null; document.addEventListener('copy', () => { window.__copied = document.activeElement.value; }, {once: true});""")
        b.click("#copy-button")
        b.wait("document.getElementById('copy-status').textContent", 2)
        check("복사 대체 경로(clipboard 거부 → execCommand)도 같은 글", b.js("window.__copied") == expect and text_of(b, "copy-status") == "복사했어요"
              and b.js("document.querySelectorAll('.copy-buffer').length") == 0, text_of(b, "copy-status"))
        b.js("document.execCommand = () => false")
        b.click("#copy-button")
        b.wait("document.getElementById('copy-status').textContent.includes('못')", 2)
        check("복사 모두 실패: 직접 복사 안내", text_of(b, "copy-status") == "복사하지 못했어요. 직접 선택해 복사해 주세요.")

        # ── 6. 드래그 앤 드롭 ──
        fresh(b)
        mock("/__mock/reset", "POST")
        DROP = """((names, types) => {
          const zone = document.getElementById('drop-zone'); const dt = new DataTransfer();
          names.forEach((n, i) => dt.items.add(new File([new TextEncoder().encode('%PDF-1.4\\nmock')], n, {type: types[i] ?? 'application/pdf'})));
          const r = zone.getBoundingClientRect(); const o = {dataTransfer: dt, bubbles: true, cancelable: true, clientX: r.left + 20, clientY: r.top + 20};
          zone.dispatchEvent(new DragEvent('dragenter', o)); zone.dispatchEvent(new DragEvent('dragover', o));
          window.__over = zone.classList.contains('is-over') && getComputedStyle(zone.querySelector('.drop__over')).display !== 'none';
          window.__overShot = true;
          if (window.__holdDrop) return;
          const d = new DragEvent('drop', o); zone.dispatchEvent(d); window.__dropPrevented = d.defaultPrevented;
        })"""
        b.js(f"window.__holdDrop = true; {DROP}(['ko.pdf'], [''])")
        b.shot("04-dragover-1280", full=False)
        check("드래그가 영역 위: 강조 + '놓으면 바로 읽기 시작해요' 문구", b.js("window.__over") is True)
        b.js("document.getElementById('drop-zone').dispatchEvent(new DragEvent('dragleave', {bubbles: true}))")
        check("드래그가 영역을 벗어나면 강조 해제", not b.js("document.getElementById('drop-zone').classList.contains('is-over')"))
        b.js(f"window.__holdDrop = false; {DROP}(['드롭한 문서.pdf'], [''])")
        b.wait(V["doc"], 5)
        lg = mlog("/api/extract")
        check("드롭(type이 빈 .pdf): 기본 동작 막고 업로드 → 읽기 완료", b.js("window.__dropPrevented") is True and b.js(V["doc"])
              and lg and lg[-1]["file_name"] == "드롭한 문서.pdf", lg)
        # 페이지 다른 곳에 놓아도 브라우저가 파일을 열지 않음
        prevented = b.js("""(() => { const dt = new DataTransfer(); dt.items.add(new File(['x'], 'a.pdf'));
            const e = new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true});
            document.querySelector('.site-header').dispatchEvent(e); return e.defaultPrevented; })()""")
        check("영역 밖 드롭: 기본 동작(파일 열기) 막음, 요청 없음", prevented is True and len(mlog("/api/extract")) == 1)

        # CDP 진짜 드래그 이벤트(Input.dispatchDragEvent)로 실제 파일 경로를 놓기
        fresh(b)
        try:
            xy = b.center("#drop-zone")
            data = {"items": [], "files": [F["UPPER.PDF"]], "dragOperationsMask": 1}
            for t in ("dragEnter", "dragOver", "drop"):
                b.call("Input.dispatchDragEvent", type=t, x=xy[0], y=xy[1], data=data)
            b.wait(V["doc"], 5)
            lg = mlog("/api/extract")
            check("CDP 드래그(Input.dispatchDragEvent, 실제 파일·대문자 .PDF) → 업로드", b.js(V["doc"]) and lg[-1]["file_name"] == "UPPER.PDF", lg[-1:])
        except RuntimeError as e:
            check("CDP 드래그(Input.dispatchDragEvent) → 업로드", False, e)

        # ── 7. 미리 거르기: 요청 없이 막힘 ──
        fresh(b)
        before = len(mlog("/api/extract"))
        b.js(f"{DROP}(['a.pdf', 'b.pdf'], ['', ''])")
        b.wait(V["error"], 3)
        check("여러 파일 드롭 → MULTIPLE_FILES 문구, 업로드 영역 그대로, 다시 시도 없음",
              EXPECT["MULTIPLE_FILES"] in text_of(b, "error-text") and b.js(V["upload"]) and b.js("document.getElementById('retry-button').hidden")
              and text_of(b, "error-title") == "이 파일은 올릴 수 없어요")
        check("오류 뒤 포커스는 [다른 파일 고르기]", b.js("document.activeElement.id") == "another-button")
        b.shot("05-error-pre-1280")
        for name, code in [("zero.pdf", "EMPTY_FILE"), ("big.pdf", "FILE_TOO_LARGE"), ("note.txt", "NOT_PDF")]:
            b.js("document.getElementById('error').hidden = true")
            upload(b, F[name], None)
            b.wait(V["error"], 3)
            check(f"미리 거르기 {name} → {code}", EXPECT[code] in text_of(b, "error-text") and code not in text_of(b, "error"), text_of(b, "error-text"))
        b.js("document.getElementById('error').hidden = true")
        b.js(f"{DROP}(['그림.png'], ['image/png'])")
        b.wait(f"document.getElementById('error-text').textContent.includes('PDF 파일만')", 3)
        check("드롭 그림.png(image/png) → NOT_PDF", EXPECT["NOT_PDF"] in text_of(b, "error-text"))
        check("미리 거른 5건 모두 서버 요청 0회", len(mlog("/api/extract")) == before and not any("/api/extract" in u for u in b.js("window.__fetches")),
              len(mlog("/api/extract")) - before)
        # 오류 상태에서도 업로드 영역으로 바로 다른 파일
        upload(b, F["ko.pdf"])
        check("오류 화면에서 바로 다른 파일 올리기 → 읽기 완료, 오류 사라짐", b.js(V["doc"]) and not b.js(V["error"]))

        # ── 8. 추출 오류 코드 ──
        for code in ["NOT_PDF", "ENCRYPTED", "BROKEN_PDF", "NO_TEXT", "TOO_LONG", "EXTRACT_TIMEOUT", "TOOL_MISSING",
                     "BAD_REQUEST", "INTERNAL", "FORBIDDEN_HOST"]:
            fresh(b)
            upload(b, F[f"err-{code}.pdf"])
            t = text_of(b, "error")
            check(f"추출 오류 {code}: 문구·코드/서버 원문 숨김·[다른 파일]만·업로드 영역 보임",
                  b.js(V["error"]) and EXPECT[code] in t and code not in t and "mock" not in t
                  and b.js("document.getElementById('retry-button').hidden") and b.js(V["upload"]) and not b.js(V["doc"])
                  and text_of(b, "error-title") == "PDF를 읽지 못했어요", t)
            if code == "TOOL_MISSING":
                check("명령어는 <code>로 표시(고정 문구)", b.js("document.querySelector('#error-text code').textContent") == "sudo apt install poppler-utils")
                b.shot("06-error-extract-1280")
        fresh(b)
        upload(b, F["fake.pdf"])
        check("이름만 .pdf인 글 파일 → 서버 NOT_PDF 문구", EXPECT["NOT_PDF"] in text_of(b, "error-text"))
        for name in ["nojson.pdf", "badshape.pdf"]:
            fresh(b)
            upload(b, F[name])
            check(f"추출 {name}(JSON 아님/모양 틀림) → 일반 오류 문구", "알 수 없는 문제" in text_of(b, "error-text"), text_of(b, "error-text"))

        # ── 9. 요약 오류 코드 ──
        retryable = {"NO_KEY", "BUSY", "RATE_LIMIT", "TIMEOUT", "UPSTREAM", "BAD_OUTPUT"}
        for code in ["NO_KEY", "BUSY", "RATE_LIMIT", "TIMEOUT", "UPSTREAM", "BAD_OUTPUT", "TOO_LONG", "BAD_REQUEST", "INTERNAL"]:
            fresh(b)
            upload(b, F[f"sum-{code}.pdf"])
            summarize(b)
            t = text_of(b, "error")
            has_retry = not b.js("document.getElementById('retry-button').hidden")
            check(f"요약 오류 {code}: 문구·코드 숨김·다시 시도 {'있음' if code in retryable else '없음'}·문서 카드 유지",
                  EXPECT[code] in t and code not in t and "mock" not in t and has_retry == (code in retryable)
                  and b.js(V["doc"]) and not b.js(V["upload"]) and text_of(b, "error-title") == "요약하지 못했어요"
                  and b.js("document.activeElement.id") == ("retry-button" if code in retryable else "another-button"), t)
            if code == "UPSTREAM":
                b.shot("07-error-summarize-1280")
                b.viewport(480); b.shot("07-error-summarize-480"); b.viewport(1280)
        for name, days in [("sum-DAILY_LIMIT.pdf", 1), ("sum-DAILY_LIMIT-noreset.pdf", 1), ("sum-DAILY_LIMIT-reset3d.pdf", 3)]:
            fresh(b)
            upload(b, F[name])
            summarize(b)
            t = text_of(b, "error-text")
            want = expected_reset(days)
            check(f"DAILY_LIMIT({name}): '{want}부터' 안내, 다시 시도 없음, 1분 안내 없음",
                  "무료 요약 횟수를 다 썼어요" in t and f"{want}부터 다시 요약할 수 있어요" in t and "1분" not in t
                  and b.js("document.getElementById('retry-button').hidden") and "DAILY_LIMIT" not in text_of(b, "error"), t)
        b.shot("08-daily-limit-1280")
        for name, code in [("sumnojson.pdf", "UPSTREAM"), ("sumbadshape.pdf", "BAD_OUTPUT")]:
            fresh(b)
            upload(b, F[name])
            summarize(b)
            check(f"요약 {name} → {code} 문구 + 다시 시도", EXPECT[code] in text_of(b, "error-text")
                  and not b.js("document.getElementById('retry-button').hidden"), text_of(b, "error-text"))

        # ── 10. 다시 시도: 업로드 없이 요약만 ──
        fresh(b)
        mock("/__mock/reset", "POST")
        upload(b, F["once-UPSTREAM.pdf"])
        summarize(b)
        check("once-UPSTREAM: 첫 요약 실패", b.js(V["error"]))
        b.click("#retry-button")
        b.wait(V["result"], 5)
        check("[다시 시도] → 결과, 추출 1회·요약 2회(다시 올리지 않음)", b.js(V["result"]) and len(mlog("/api/extract")) == 1
              and len(mlog("/api/summarize")) == 2, [(e["path"], e["status"]) for e in mlog()])

        # ── 11. 요약 중: 경과 초·연타 방지·긴 문서 안내 ──
        fresh(b)
        mock("/__mock/reset", "POST")
        upload(b, F["long-sumslow-2.pdf"])
        check("긴 문서: 잘림 안내 '앞 27쪽(약 60,000자)까지만 요약합니다'",
              text_of(b, "doc-truncated") == "문서가 길어 앞 27쪽(약 60,000자)까지만 요약합니다." and text_of(b, "doc-chars") == "129,579자 중 60,000자",
              [text_of(b, "doc-truncated"), text_of(b, "doc-chars")])
        b.js("navigator.clipboard.writeText = (t) => { window.__copied = t; return Promise.resolve(); }")
        b.click("#summarize-button")
        b.js("for (let i = 0; i < 5; i++) document.getElementById('summarize-button').click()")
        b.wait(V["progress"], 2)
        check("요약 중: 진행 카드·'요약하고 있어요… N초'·버튼 잠김·취소에 포커스",
              text_of(b, "progress-title").startswith("요약하고 있어요…") and b.js("document.getElementById('summarize-button').disabled")
              and b.js("document.activeElement.id") == "cancel-button")
        check("요약 중: 긴 문서 안내 '30초~1분'(QA 실측으로 바꿈)", "긴 문서라 30초~1분쯤 걸릴 수 있어요" in text_of(b, "progress-hint"))
        b.shot("09-summarizing-1280", full=False)
        b.viewport(480); b.shot("09-summarizing-480", full=False); b.viewport(1280)
        time.sleep(1.2)
        sec = text_of(b, "progress-title")
        check("요약 중: 경과 초가 1초마다 오름", sec != "요약하고 있어요… 0초", sec)
        b.wait(V["result"], 5)
        check("[요약하기] 연타해도 요약 요청 1번", len(mlog("/api/summarize")) == 1 and sum("/api/summarize" in u for u in b.js("window.__fetches")) == 1)
        check("긴 문서 결과: 잘림 안내 + 호출 4회(3조각+합치기) 표시", "앞 27쪽까지만 요약했어요" in text_of(b, "result-truncated") and "호출 4회" in text_of(b, "result-meta"),
              [text_of(b, "result-truncated"), text_of(b, "result-meta")])
        b.click("#copy-button")
        b.wait("window.__copied", 2)
        check("긴 문서 복사: 마지막 줄 '※ 앞 27쪽까지만 요약했습니다.'", (b.js("window.__copied") or "").endswith("\n\n※ 앞 27쪽까지만 요약했습니다."))
        fresh(b)
        upload(b, F["mixed.pdf"])
        check("빈 쪽 안내: '글자가 없는 쪽 1개는 빼고 읽었어요'", text_of(b, "doc-empty-pages") == "글자가 없는 쪽 1개는 빼고 읽었어요(스캔된 쪽일 수 있어요).")
        fresh(b)
        upload(b, F["notitle.pdf"])
        check("제목이 비면 파일 이름을 제목으로(중복 표시 없음)", text_of(b, "doc-title") == "notitle.pdf" and b.js("document.getElementById('doc-file').hidden"))

        # ── 12. 취소 · BUSY · 늦은 응답 ──
        fresh(b)
        mock("/__mock/reset", "POST")
        upload(b, F["sumslow-3.pdf"])
        b.click("#summarize-button")
        b.wait(V["progress"], 2)
        b.click("#cancel-button")
        check("요약 취소: 읽기 완료로 돌아가 '요약을 멈췄어요' + [요약하기]에 포커스",
              b.js(V["doc"]) and not b.js(V["progress"]) and not b.js("document.getElementById('doc-stopped').hidden")
              and b.js("document.activeElement.id") == "summarize-button")
        summarize(b)
        check("취소 직후 다시 요약 → 서버가 아직 바쁨(BUSY) 문구 + 다시 시도", EXPECT["BUSY"] in text_of(b, "error-text")
              and not b.js("document.getElementById('retry-button').hidden"))
        time.sleep(3.2)
        b.click("#retry-button")
        b.wait(V["result"], 6)
        check("잠금이 풀린 뒤 다시 시도 → 결과", b.js(V["result"]))

        fresh(b)
        mock("/__mock/reset", "POST")
        upload(b, F["slow-2.pdf"], None)
        b.wait(V["progress"], 2)
        check("읽는 중: 파일 이름·'PDF에서 글자를 읽고 있어요…'·업로드 영역 비활성",
              "slow-2.pdf" in text_of(b, "progress-file") and text_of(b, "progress-title") == "PDF에서 글자를 읽고 있어요…"
              and b.js("document.getElementById('pick-button').disabled") and b.js("document.getElementById('drop-zone').classList.contains('is-disabled')"))
        b.shot("10-extracting-1280", full=False)
        b.js(f"{DROP}(['ko.pdf'], [''])")
        b.set_files([F["ko.pdf"]])
        time.sleep(0.3)
        check("읽는 중 새 파일 드롭·선택은 무시(요청 1건)", sum("/api/extract" in u for u in b.js("window.__fetches")) == 1)
        b.click("#cancel-button")
        check("읽기 취소 → 처음 화면, 고르기 버튼에 포커스", b.js(V["upload"]) and not b.js(V["progress"]) and b.js("document.activeElement.id") == "pick-button")
        upload(b, F["mixed.pdf"])
        time.sleep(2.3)
        check("취소한 이전 응답이 새 화면을 덮지 않음", text_of(b, "doc-pages") == "6쪽" and not b.js(V["error"]))

        # ── 13. 서버 꺼짐(NETWORK) — 추출·요약 각각 다시 시도 ──
        fresh(b)
        mock("/__mock/reset", "POST")
        b.js("window.__realFetch = window.fetch; window.fetch = (u, o) => String(u).includes('/api/extract') ? Promise.reject(new TypeError('Failed to fetch')) : window.__realFetch(u, o)")
        upload(b, F["ko.pdf"])
        check("추출 NETWORK: run.sh 안내 + 다시 시도", "./run.sh" in text_of(b, "error-text") and not b.js("document.getElementById('retry-button').hidden"))
        b.js("window.fetch = window.__realFetch")
        b.click("#retry-button")
        b.wait(V["doc"], 5)
        check("추출 [다시 시도] → 같은 파일로 다시 읽음", b.js(V["doc"]) and [e["file_name"] for e in mlog("/api/extract")] == ["ko.pdf"])
        b.js("window.fetch = (u, o) => String(u).includes('/api/summarize') ? Promise.reject(new TypeError('Failed to fetch')) : window.__realFetch(u, o)")
        summarize(b)
        check("요약 NETWORK: 안내 + 다시 시도, 문서 카드 유지", "./run.sh" in text_of(b, "error-text") and b.js(V["doc"]))
        b.js("window.fetch = window.__realFetch")
        b.click("#retry-button")
        b.wait(V["result"], 5)
        check("요약 [다시 시도] → 결과, 추출은 다시 안 함", b.js(V["result"]) and len(mlog("/api/extract")) == 1)

        # ── 14. 보안: 문자열은 글자로만 ──
        fresh(b)
        upload(b, F["xss.pdf"])
        summarize(b)
        check("파일·모델 문자열의 HTML이 해석되지 않음", b.js("window.__xss") is None
              and b.js("document.querySelectorAll('main img, main script, main b').length") == 0
              and "<img" in text_of(b, "doc-title") and "<img" in text_of(b, "result-one")
              and "<b>굵게</b>" in text_of(b, "result-points"))

        # ── 15. 미리 안내(/api/health) ──
        for mode, want in [("notools", ["sudo apt install poppler-utils"]), ("nokey", ["python3 -B config.py", "요약은 할 수 없어요"]),
                           ("both", ["poppler-utils", "config.py"]), ("down", ["./run.sh"])]:
            mock(f"/__mock/health?mode={mode}", "POST")
            fresh(b)
            b.wait("document.getElementById('health-notices').children.length > 0", 3)
            t = text_of(b, "health-notices")
            check(f"health {mode}: 업로드 위에 미리 안내, 업로드는 그대로 됨", all(w in t for w in want) and b.js(V["upload"])
                  and not b.js("document.getElementById('pick-button').disabled"), t)
            if mode == "both":
                b.shot("11-health-1280")
                c = b.js(CONTRAST_JS)
                check("명암비 AA: 미리 안내", not c["bad"], c)
        mock("/__mock/health?mode=ok", "POST")

        # ── 16. 화면 크기별 · 명암비 ──
        fresh(b)
        layout_checks(b, "12-ready")
        c = b.js(CONTRAST_JS)
        check("명암비 AA: 처음 화면", not c["bad"], c)
        upload(b, F["longword.pdf"])
        layout_checks(b, "13-extracted-longword")
        summarize(b)
        layout_checks(b, "14-result-longword")
        c = b.js(CONTRAST_JS)
        check("명암비 AA: 결과", not c["bad"], c)
        fresh(b)
        upload(b, F["sum-NO_KEY.pdf"])
        summarize(b)
        layout_checks(b, "15-error-nokey")
        c = b.js(CONTRAST_JS)
        check("명암비 AA: 오류(<code> 포함)", not c["bad"], c)
        fresh(b)
        upload(b, F["long-sumslow-2.pdf"])
        b.click("#summarize-button")
        b.wait(V["progress"], 2)
        c = b.js(CONTRAST_JS)
        check("명암비 AA: 요약 중(비활성 버튼 포함)", not c["bad"], c)
        for w in (480, 360):
            b.viewport(w)
            ov = overflow(b)
            check(f"요약 중 {w}px 가로 넘침 없음", ov["scroll"] <= ov["client"] and not ov["wide"], ov)
            b.shot(f"16-summarizing-{w}", full=False)
        b.viewport(1280)
        b.wait(V["result"], 5)
        b.viewport(480)
        check("480px: 버튼이 가로 꽉 참", b.js("""(() => { const b = document.getElementById('copy-button').getBoundingClientRect();
              const c = document.getElementById('result').getBoundingClientRect(); return b.width > c.width - 60; })()"""))
        b.viewport(1280)
        check("넓은 화면: 본문 최대 폭 760px 가운데", b.js("""(() => { const r = document.getElementById('main').getBoundingClientRect();
              return Math.abs(r.width - 760) < 2 && Math.abs(r.left - (document.documentElement.clientWidth - r.right)) < 2; })()"""))

        # ── 17. 움직임 줄이기 ──
        b.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
        anim = b.js("[getComputedStyle(document.querySelector('.spinner')).animationName, getComputedStyle(document.querySelector('.bar__fill')).animationName]")
        check("prefers-reduced-motion: 회전·막대 애니메이션 멈춤", anim == ["none", "none"], anim)
        b.call("Emulation.setEmulatedMedia", features=[])

        # ── 18. 클라이언트 제한시간(CLIENT_TIMEOUT) — 제한시간을 1.5초로 줄인 페이지에서 ──
        ident = b.call("Page.addScriptToEvaluateOnNewDocument",
                       source="globalThis.__pdfSummaryTimeouts = {extract: 1500, summarize: 1500};")["identifier"]
        time.sleep(2.5)   # 앞의 요약(2초) 잠금이 풀리길
        fresh(b)
        upload(b, F["slow-3.pdf"], None)
        b.wait(V["error"], 5)
        check("추출 CLIENT_TIMEOUT: 읽기 단계 문구 + 다시 시도", "PDF를 읽는 데 너무 오래 걸려" in text_of(b, "error-text")
              and "요약" not in text_of(b, "error-text") and not b.js("document.getElementById('retry-button').hidden"), text_of(b, "error-text"))
        upload(b, F["sumslow-2.pdf"])
        summarize(b)
        check("요약 CLIENT_TIMEOUT: '요약이 너무 오래 걸려' + 다시 시도", EXPECT["CLIENT_TIMEOUT"] in text_of(b, "error-text")
              and not b.js("document.getElementById('retry-button').hidden"), text_of(b, "error-text"))
        b.call("Page.removeScriptToEvaluateOnNewDocument", identifier=ident)
        time.sleep(1.5)

        # ── 19. 콘솔 ──
        errs = b.errors()
        check("콘솔 오류·경고 0건(CSP 위반·파일 고르기 차단 포함)", not errs, errs[:5])
        net = b.network_errors()
        check("네트워크 오류 로그는 의도한 4xx/5xx뿐", all("status of" in t for t in net), f"{len(net)}건: {net[:2]}")

        # ── 20. 파일로 직접 열기 ──
        b.events.clear()
        b.goto((ROOT / "index.html").as_uri())
        time.sleep(1.6)
        vis = b.js("getComputedStyle(document.getElementById('boot-notice')).visibility")
        check("file:// 로 열면 run.sh·8030 안내만 보임(기능 숨김)", vis == "visible" and "./run.sh" in text_of(b, "boot-notice")
              and "127.0.0.1:8030" in text_of(b, "boot-notice") and b.js("getComputedStyle(document.getElementById('main')).display") == "none", vis)
        b.shot("17-file-1280", full=False)
        b.viewport(360); b.shot("17-file-360", full=False)
        ov = overflow(b)
        check("file:// 안내 360px 가로 넘침 없음", ov["scroll"] <= ov["client"], ov)
    finally:
        if b:
            b.close()
        stop(server)
        shutil.rmtree(OUT / "chrome-profile", ignore_errors=True)

    passed = sum(ok for _, ok, _ in results)
    print("\n" + "=" * 60)
    print(f"프런트엔드 점검 {passed}/{len(results)} 통과  (스크린샷: {OUT})")
    for name, ok, _ in results:
        if not ok:
            print("  실패:", name)
    print(f"포트 {PORT}: {'아직 열려 있음!' if port_busy(PORT) else '닫힘'}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
