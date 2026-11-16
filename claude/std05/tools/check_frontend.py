#!/usr/bin/env python3
"""프런트엔드 화면 점검. 개발 전용 — 앱은 이 파일을 로드하지 않는다.

playwright·selenium이 없어서 표준 라이브러리만으로 headless Chromium을 CDP로 조종한다.
tools/mock_server.py를 빈 포트에 띄워 성공·오류·느린 응답·저장 실패 등을 재현하고,
넓은 화면·480px·360px 스크린샷을 저장소 밖 폴더에 남긴다. 끝나면 서버와 브라우저를 끈다.

    python3 tools/check_frontend.py [--out 스크린샷_폴더]
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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else Path(tempfile.gettempdir()) / "std05-frontend-check"
DEVNULL = subprocess.DEVNULL
KEY = "diary.v1.entries"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def stop(proc) -> None:
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()


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
        port = free_port()
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
        for d in ("Page", "Runtime", "Log", "Network"):
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
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]
            raise RuntimeError(f"JS 오류: {d.get('text')} {d.get('exception', {}).get('description')}")
        return r["result"].get("value")

    def wait(self, expr: str, timeout: float = 10.0):
        end = time.time() + timeout
        while time.time() < end:
            try:
                v = self.js(expr)
            except RuntimeError:          # 페이지가 바뀌는 중이면 문서가 비어 있을 수 있다
                v = None
            if v:
                return v
            time.sleep(0.1)
        return self.js(expr)

    def goto(self, url: str, ready: str = "document.readyState === 'complete'") -> None:
        self.js("window.__old = true")
        self.call("Page.navigate", url=url)
        self.wait(f"!window.__old && {ready}", 15)

    def viewport(self, width: int, height: int = 900) -> None:
        self.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)

    def shot(self, name: str, full: bool = True) -> Path:
        params = {"format": "png", "captureBeyondViewport": full}
        data = self.call("Page.captureScreenshot", **params)["data"]
        p = OUT / f"{name}.png"
        p.write_bytes(base64.b64decode(data))
        return p

    def key(self, key: str, code: str, vk: int, text: str = "") -> None:
        for t in ("keyDown", "keyUp"):
            self.call("Input.dispatchKeyEvent", type=t, key=key, code=code, windowsVirtualKeyCode=vk,
                      **({"text": text} if text and t == "keyDown" else {}))

    def errors(self) -> list[str]:
        """앱 코드에서 난 오류만. 서버가 4xx/5xx를 준 네트워크 로그는 의도한 것이라 뺀다."""
        out = []
        for e in self.events:
            if e["method"] == "Runtime.exceptionThrown":
                out.append(e["params"]["exceptionDetails"].get("exception", {}).get("description", "exception"))
            elif e["method"] == "Runtime.consoleAPICalled" and e["params"]["type"] == "error":
                out.append("console.error: " + str(e["params"]["args"]))
            elif e["method"] == "Log.entryAdded" and e["params"]["entry"]["level"] == "error" \
                    and e["params"]["entry"].get("source") != "network":
                out.append("log: " + e["params"]["entry"]["text"])
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


# ── 점검 ────────────────────────────────────────────────

results: list[tuple[str, bool, str]] = []


def check(name: str, ok, evidence="") -> None:
    results.append((name, bool(ok), str(evidence)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n       {evidence}" if evidence else ""), flush=True)


SPY = """
window.__fetchCount = 0;
const __f = window.fetch;
window.fetch = function () { window.__fetchCount++; return __f.apply(this, arguments); };
"""

S = {
    "result_visible": "!document.getElementById('result').hidden",
    "error_visible": "!document.getElementById('error-box').hidden",
    "waiting_visible": "!document.getElementById('waiting').hidden",
    "idle": "document.getElementById('waiting').hidden",
}


def submit(b: Browser, text: str) -> None:
    b.js(f"""(() => {{ const i = document.getElementById('entry-input'); i.focus(); i.value = {json.dumps(text)};
          i.dispatchEvent(new Event('input', {{bubbles: true}}));
          document.getElementById('entry-form').requestSubmit(); }})()""")


def done(b: Browser, timeout=10) -> None:
    b.wait(f"{S['idle']} && ({S['result_visible']} || {S['error_visible']})", timeout)


def stored(b: Browser):
    return b.js(f"(() => {{ try {{ return JSON.parse(localStorage.getItem('{KEY}')) }} catch (e) {{ return 'ERR' }} }})()")


def overflow(b: Browser) -> dict:
    return b.js("""(() => {
      const de = document.documentElement;
      const wide = [...document.querySelectorAll('body *')].filter(e => {
        const r = e.getBoundingClientRect(); return r.width && (r.right > de.clientWidth + 1 || r.left < -1) && !e.closest('.visually-hidden, .skip-link');
      }).map(e => e.tagName + '.' + e.className).slice(0, 5);
      return { scroll: de.scrollWidth, client: de.clientWidth, wide };
    })()""")


def small_targets(b: Browser) -> list:
    return b.js("""[...document.querySelectorAll('button, a[href], input, summary')].filter(e => {
        if (e.closest('.visually-hidden') || e.classList.contains('skip-link')) return false;
        const r = e.getBoundingClientRect(); if (!r.width && !r.height) return false;
        return r.width < 44 || r.height < 44;
      }).map(e => `${e.tagName} "${(e.textContent||e.value||'').trim().slice(0,20)}" ${Math.round(e.getBoundingClientRect().width)}x${Math.round(e.getBoundingClientRect().height)}`)""")


CONTRAST_JS = r"""(() => {
  const parse = c => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const lum = ({r,g,b}) => { const f = v => { v /= 255; return v <= .03928 ? v/12.92 : ((v+.055)/1.055) ** 2.4; }; return .2126*f(r) + .7152*f(g) + .0722*f(b); };
  const bgOf = el => { for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > .5) return c; } return { r: 255, g: 255, b: 255, a: 1 }; };
  const bad = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  while (walker.nextNode()) {
    const t = walker.currentNode; const el = t.parentElement;
    if (!t.textContent.trim() || seen.has(el)) continue; seen.add(el);
    if (el.closest('[hidden], .visually-hidden, template, .skip-link, button:disabled')) continue;
    const r = el.getBoundingClientRect(); if (!r.width) continue;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden') continue;
    const fg = parse(cs.color), bg = bgOf(el);
    const L1 = lum(fg), L2 = lum(bg); const ratio = (Math.max(L1,L2)+.05)/(Math.min(L1,L2)+.05);
    const size = parseFloat(cs.fontSize), bold = +cs.fontWeight >= 700;
    const need = (size >= 24 || (bold && size >= 18.66)) ? 3 : 4.5;
    if (ratio < need) bad.push(`${el.tagName}.${el.className} "${t.textContent.trim().slice(0,15)}" ${ratio.toFixed(2)}`);
  }
  // placeholder
  const i = document.getElementById('entry-input');
  const ph = parse(getComputedStyle(i, '::placeholder').color), ib = bgOf(i);
  const pr = (Math.max(lum(ph),lum(ib))+.05)/(Math.min(lum(ph),lum(ib))+.05);
  return { bad, placeholder: +pr.toFixed(2) };
})()"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    port = free_port()
    server = subprocess.Popen([sys.executable, str(ROOT / "tools" / "mock_server.py"), str(port)], stdout=DEVNULL, stderr=DEVNULL)
    base = f"http://127.0.0.1:{port}/"
    b = None
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(base, timeout=1); break
            except Exception:
                time.sleep(0.1)
        b = Browser()
        b.call("Page.addScriptToEvaluateOnNewDocument", source=SPY)
        b.viewport(1280)
        ready = "document.documentElement.classList.contains('app-ready')"
        b.goto(base, ready)

        # 1. 첫 화면
        check("첫 화면: 앱 준비, 안내문 숨김", b.js(ready) and b.js("getComputedStyle(document.getElementById('boot-notice')).display") == "none")
        check("첫 화면: 빈 목록 안내", b.js("!document.getElementById('history-empty').hidden"))
        b.shot("01-empty-wide")

        # 2. 빈 입력
        submit(b, "   ")
        check("빈 입력은 보내지 않음", b.js("window.__fetchCount") == 0 and b.js("!document.getElementById('entry-hint').hidden"),
              b.js("document.getElementById('entry-hint').textContent"))

        # 3. 글자 수
        b.js("(() => { const i = document.getElementById('entry-input'); i.value = '가'.repeat(190); i.dispatchEvent(new Event('input')); })()")
        check("남은 글자 수 표시", b.js("document.getElementById('entry-count').textContent") == "10자 더 쓸 수 있어요")
        check("maxlength 200", b.js("document.getElementById('entry-input').maxLength") == 200)

        # 4. 감정 8개
        for i, emo in enumerate(["기쁨", "설렘", "평온", "슬픔", "불안", "분노", "지침", "외로움"]):
            submit(b, f"{emo} 세기{i % 5 + 1} 오늘 있었던 일")
            done(b)
            got = b.js("[document.getElementById('result-emotion').textContent, document.getElementById('result-badge').textContent, document.getElementById('result-intensity').textContent, document.getElementById('result').dataset.emotion]")
            check(f"성공: {emo}", got[0] == emo and got[3] == emo and f"5단계 중 {i % 5 + 1}" in got[2], got)
        n = b.js("document.querySelectorAll('#history-list > li').length")
        st = stored(b)
        check("8개 모두 저장·목록 최신순", n == 8 and isinstance(st, list) and len(st) == 8 and st[0]["emotion"] == "외로움"
              and b.js("document.querySelector('#history-list > li').dataset.emotion") == "외로움", f"목록 {n}, 저장 {len(st) if isinstance(st, list) else st}")
        check("성공 뒤 입력칸 비움", b.js("document.getElementById('entry-input').value") == "")
        b.shot("02-result-wide")

        # 5. 엔터 제출 · IME 조합 중 엔터
        before = b.js("window.__fetchCount")
        prevented = b.js("""(() => { const i = document.getElementById('entry-input'); i.value = '평온 조합중';
            const e = new KeyboardEvent('keydown', {key: 'Enter', isComposing: true, bubbles: true, cancelable: true});
            i.dispatchEvent(e); return e.defaultPrevented; })()""")
        check("조합 중 엔터는 기본 동작 막음", prevented is True and b.js("window.__fetchCount") == before)
        b.js("document.getElementById('entry-input').focus()")
        b.key("Enter", "Enter", 13, "\r")
        b.wait("window.__fetchCount > " + str(before), 3)
        check("진짜 엔터 키로 제출", b.js("window.__fetchCount") == before + 1)
        done(b)

        # 6. crisis
        submit(b, "슬픔 위기 너무 힘들어요")
        done(b)
        crisis = b.js("[...document.querySelectorAll('#result-crisis a')].map(a => [a.getAttribute('href'), a.textContent.trim()])")
        check("crisis: 도움받을 곳 표시(109, 1577-0199, tel:)", crisis and crisis[0][0] == "tel:109" and "109" in crisis[0][1]
              and crisis[1][0] == "tel:1577-0199" and "1577-0199" in crisis[1][1]
              and "24시간" in b.js("document.getElementById('result-crisis').textContent"), crisis)
        check("crisis: 지난 일기에도 도움 번호", b.js("document.querySelector('#history-list > li .entry__help') !== null"))
        b.shot("03-crisis-wide")
        b.viewport(480); b.shot("03-crisis-480"); b.viewport(1280)

        # 7. 사용자 글은 글자로만
        submit(b, '<img src=x onerror="window.__xss=1"> 기쁨')
        done(b)
        check("사용자 글이 HTML로 해석되지 않음", b.js("window.__xss") is None and b.js("document.querySelectorAll('#result img, #history-list img').length") == 0
              and "<img" in b.js("document.getElementById('result-quote').textContent"))

        # 8. 오류 코드
        for code in ["BAD_REQUEST", "TOO_LONG", "NO_KEY", "RATE_LIMIT", "TIMEOUT", "UPSTREAM", "BAD_OUTPUT"]:
            submit(b, f"오류:{code} 시험")
            done(b)
            text = b.js("document.getElementById('error-box').textContent")
            check(f"오류 {code}: 따뜻한 안내, 코드 숨김, 입력 유지",
                  b.js(S["error_visible"]) and code not in text and "mock" not in text
                  and b.js("document.getElementById('entry-input').value") == f"오류:{code} 시험", " ".join(text.split()))
        submit(b, "오류:DAILY_LIMIT 시험")
        done(b)
        text = " ".join(b.js("document.getElementById('error-box').textContent").split())
        check("오류 DAILY_LIMIT: '내일 오전 9시부터' 안내, 코드 숨김, 입력 유지",
              b.js(S["error_visible"]) and "내일 오전 9시부터" in text and "DAILY_LIMIT" not in text
              and "1분" not in text and b.js("document.getElementById('entry-input').value") == "오류:DAILY_LIMIT 시험", text)
        b.shot("04-error-daily")
        b.shot("04-error-wide")
        for label, txt in [("JSON 아닌 응답", "비JSON 시험"), ("모양이 틀린 성공({})", "빈성공 시험")]:
            submit(b, txt); done(b)
            check(f"{label}: 오류 화면으로", b.js(S["error_visible"]), " ".join(b.js("document.getElementById('error-text').textContent").split()))
        submit(b, "이상값 시험"); done(b)
        check("계약 밖 값(놀람, 세기 9): 기본 이모지·세기 숨김", b.js(S["result_visible"]) and b.js("document.getElementById('result-badge').textContent") == "📝"
              and b.js("document.getElementById('result-intensity').textContent") == "" and b.js("document.getElementById('result-crisis').children.length") == 0)
        submit(b, "긴답 지침"); done(b)
        b.viewport(360)
        ov = overflow(b)
        check("띄어쓰기 없는 긴 답장도 360px에서 가로 넘침 없음", ov["scroll"] <= ov["client"] and not ov["wide"], ov)
        b.viewport(1280)

        # 9. 느린 응답: 대기 화면, 중복 방지, 취소 뒤 새 제출, 늦은 응답 무시
        count0 = b.js("window.__fetchCount")
        n0 = len(stored(b))
        submit(b, "느림:4 기쁨 느린 응답")
        b.wait(S["waiting_visible"], 2)
        check("대기 중: 안내 표시·버튼 잠김·aria-busy", b.js(S["waiting_visible"]) and b.js("document.getElementById('entry-submit').disabled")
              and b.js("document.getElementById('entry-form').getAttribute('aria-busy')") == "true",
              b.js("document.getElementById('waiting-phrase').textContent"))
        b.shot("05-waiting-wide")
        b.viewport(480); b.shot("05-waiting-480", full=False); b.viewport(1280)
        b.js("document.getElementById('entry-form').requestSubmit()")
        b.js("document.getElementById('entry-form').requestSubmit()")
        check("대기 중 중복 제출 막음", b.js("window.__fetchCount") == count0 + 1)
        b.js("document.getElementById('cancel-button').click()")
        check("그만 기다리기 → 다시 보낼 수 있음", b.js(S["idle"]) and not b.js("document.getElementById('entry-submit').disabled"))
        submit(b, "슬픔 새로 쓴 글")
        done(b)
        time.sleep(4.5)
        st = stored(b)
        check("늦게 온 이전 응답이 화면·기록을 덮지 않음",
              b.js("document.getElementById('result-emotion').textContent") == "슬픔" and len(st) == n0 + 1
              and not any("느린 응답" in e["text"] for e in st), f"결과 {b.js('document.getElementById(\"result-emotion\").textContent')}, 저장 {len(st)}")

        # 10. 서버 꺼짐(네트워크 실패)
        b.js("window.__realFetch = window.fetch; window.fetch = () => Promise.reject(new TypeError('Failed to fetch'))")
        submit(b, "기쁨 서버 꺼짐"); done(b)
        check("서버 꺼짐: 연결 안내", "run.sh" in b.js("document.getElementById('error-text').textContent"),
              " ".join(b.js("document.getElementById('error-box').textContent").split()))
        b.js("window.fetch = window.__realFetch")

        # 11. 지우기 (페이지 안 확인)
        n0 = b.js("document.querySelectorAll('#history-list > li').length")
        first_id = b.js("document.querySelector('#history-list > li').dataset.id")
        b.js("document.querySelector('#history-list [data-action=ask-delete]').click()")
        check("지우기: 확인 문구 표시, 포커스는 '그대로 둘게요'",
              b.js("document.activeElement.dataset.action") == "cancel-delete" and "지울까요" in b.js("document.querySelector('#history-list > li').textContent"))
        b.shot("06-confirm-wide", full=True)
        b.key("Escape", "Escape", 27)
        check("Esc로 확인 닫힘", b.js("document.querySelector('#history-list [data-action=confirm-delete]')") is None
              and b.js("document.activeElement.dataset.action") == "ask-delete")
        b.js("document.querySelector('#history-list [data-action=ask-delete]').click()")
        b.js("document.querySelector('#history-list [data-action=confirm-delete]').click()")
        check("지울게요 → 목록·저장소에서 제거", b.js("document.querySelectorAll('#history-list > li').length") == n0 - 1
              and not any(e["id"] == first_id for e in stored(b)))

        # 12. 저장 실패(용량 초과)
        n0 = len(stored(b))
        b.js("""window.__setItem = Storage.prototype.setItem;
                Storage.prototype.setItem = function () { throw new DOMException('full', 'QuotaExceededError'); }""")
        submit(b, "기쁨 저장 실패 시험"); done(b)
        check("용량 초과: 답장은 보이고, 목록엔 안 넣고, 실패를 알림",
              b.js(S["result_visible"]) and len(stored(b)) == n0 and b.js("document.querySelectorAll('#history-list > li').length") == n0
              and "가득" in b.js("document.getElementById('result-saved').textContent"), b.js("document.getElementById('result-saved').textContent"))
        check("용량 초과: 입력한 글은 남김", b.js("document.getElementById('entry-input').value") == "기쁨 저장 실패 시험")
        b.shot("07-quota-wide")
        b.js("document.querySelector('#history-list [data-action=ask-delete]').click()")
        b.js("document.querySelector('#history-list [data-action=confirm-delete]').click()")
        check("용량 초과 중 지우기 실패 → 항목 유지·안내", b.js("document.querySelectorAll('#history-list > li').length") == n0
              and not b.js("document.getElementById('storage-notice').hidden"))
        b.js("Storage.prototype.setItem = window.__setItem")

        # 화면 크기별 · 접근성 (데이터가 찬 상태에서)
        b.goto(base, ready)
        submit(b, "슬픔 세기4 위기 회사에서 하루 종일 혼났어요"); done(b)
        for w in (1280, 480, 360):
            b.viewport(w, 900)
            time.sleep(0.2)
            ov = overflow(b)
            check(f"{w}px 가로 넘침 없음", ov["scroll"] <= ov["client"] and not ov["wide"], ov)
            st_small = small_targets(b)
            check(f"{w}px 터치 대상 44px 이상", not st_small, st_small)
            b.shot(f"08-full-{w}")
        b.viewport(1280)
        c = b.js(CONTRAST_JS)
        check("명암비 AA(본문 4.5:1, 큰 글 3:1)", not c["bad"] and c["placeholder"] >= 4.5, c)
        b.js("document.querySelector('#history-list > li details').open = true")
        c = b.js(CONTRAST_JS)
        check("명암비 AA(펼친 답장 포함)", not c["bad"], c)
        check("aria-live 알림 영역", b.js("document.getElementById('announcer').getAttribute('aria-live')") == "polite"
              and "답장이 왔어요" in b.wait("document.getElementById('announcer').textContent", 2))
        # 키보드: Tab으로 건너뛰기 링크 → 입력칸 → 버튼
        b.goto(base, ready)
        seq = []
        for _ in range(3):
            b.key("Tab", "Tab", 9)
            seq.append(b.js("document.activeElement.id || document.activeElement.className"))
        outline = b.js("getComputedStyle(document.activeElement).outlineStyle + ' ' + getComputedStyle(document.activeElement).outlineWidth")
        check("키보드 Tab 순서·포커스 표시", seq[0] == "skip-link" and seq[1] == "entry-input" and seq[2] == "entry-submit" and outline.startswith("solid"), f"{seq} {outline}")

        # 13. 깨진 저장 데이터
        b.js(f"localStorage.setItem('{KEY}', '{{깨진 데이터')")
        b.goto(base, ready)
        check("깨진 저장 데이터: 앱 동작·안내", b.js(ready) and not b.js("document.getElementById('storage-notice').hidden")
              and b.js("localStorage.getItem('diary.v1.corrupt-backup')") == "{깨진 데이터",
              b.js("document.getElementById('storage-notice').textContent"))
        b.js(f"""localStorage.setItem('{KEY}', JSON.stringify([{{id:'a', at:'2026-01-01T00:00:00Z', text:'좋아', emotion:'기쁨', intensity:2, message:'좋네요', crisis:false}},
                 null, 3, {{id:'b'}}, {{id:'c', at:'nope', text:'x', emotion:'x', message:'x'}}]))""")
        b.goto(base, ready)
        check("일부 항목만 깨짐: 성한 것만 표시", b.js("document.querySelectorAll('#history-list > li').length") == 1
              and "4개" in b.js("document.getElementById('storage-notice').textContent"))
        submit(b, "평온 깨진 뒤 새 글"); done(b)
        check("깨진 뒤에도 새 일기 저장", len(stored(b)) == 2)

        # 14. 저장소 차단
        ident = b.call("Page.addScriptToEvaluateOnNewDocument",
                       source="Object.defineProperty(window, 'localStorage', { get() { throw new DOMException('blocked', 'SecurityError'); } });")["identifier"]
        b.goto(base, ready)
        check("저장소 차단: 앱 동작·안내", b.js(ready) and "막혀" in b.js("document.getElementById('storage-notice').textContent"))
        submit(b, "기쁨 저장소 차단"); done(b)
        check("저장소 차단: 답장은 보이고 저장 안 됨을 알림", b.js(S["result_visible"]) and "저장되지 않았어요" in b.js("document.getElementById('result-saved').textContent")
              and b.js("document.querySelectorAll('#history-list > li').length") == 0)
        b.shot("09-blocked-wide")
        b.call("Page.removeScriptToEvaluateOnNewDocument", identifier=ident)

        errs = b.errors()
        check("콘솔 오류 없음(앱 코드)", not errs, errs[:5])
        net = b.network_errors()
        check("네트워크 오류 로그는 의도한 4xx/5xx뿐", all("status of" in t for t in net), f"{len(net)}건")

        # 15. 파일로 직접 열기
        b.events.clear()
        b.goto((ROOT / "index.html").as_uri())
        time.sleep(1.6)
        vis = b.js("getComputedStyle(document.getElementById('boot-notice')).visibility")
        check("file:// 로 열면 run.sh 안내가 보임", vis == "visible" and "run.sh" in b.js("document.getElementById('boot-notice').textContent"), vis)
        b.shot("10-file-wide", full=False)
        b.viewport(360); b.shot("10-file-360", full=False)
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
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
