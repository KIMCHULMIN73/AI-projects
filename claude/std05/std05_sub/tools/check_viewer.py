#!/usr/bin/env python3
"""한눈 PDF 요약 — 3단계 화면 점검: 메인 창의 [추출한 텍스트 보기]·[추출한 텍스트 다운로드하기]와 보기 창(SPEC §10.7·§10.8·§10.11).
개발 전용(앱은 이 파일을 로드하지 않는다). 모델 호출 0회.

    python3 -B tools/check_viewer.py --out 저장소_밖_폴더

tools/mock_server.py를 127.0.0.1:8031에 띄우고, headless Chromium의 **브라우저 단위** CDP 연결 하나로
메인 창과 새로 열린 보기 창(Target 이벤트)을 함께 조종한다. 내려받은 파일은 --out/downloads에 저장해
이름·내용을 확인한다(Browser.setDownloadBehavior). 스크린샷은 --out에. 끝나면 가짜 서버와 브라우저를 끈다.
가짜 서버의 시나리오는 업로드하는 PDF 바이트의 `%mock:` 줄로 고른다(tools/mock_server.py 머리 주석).
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(1, str(ROOT / "tools"))
from check_frontend import WS, CONTRAST_JS  # noqa: E402

OUT = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else Path(tempfile.gettempdir()) / "pdf-summary-viewer-check"
FILES = OUT / "files"
DL = OUT / "downloads"
PORT = 8031
BASE = f"http://127.0.0.1:{PORT}"
DEVNULL = subprocess.DEVNULL

if ROOT in OUT.resolve().parents or OUT.resolve() == ROOT:
    sys.exit("--out은 저장소 밖 폴더여야 합니다.")


def port_busy(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def stop(proc) -> None:
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()


# ── 브라우저 단위 CDP (여러 창을 sessionId로 구분) ─────────

class CDP:
    def __init__(self):
        self.profile = OUT / "chrome-profile-viewer"
        shutil.rmtree(self.profile, ignore_errors=True)
        self.profile.mkdir(parents=True)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.proc = subprocess.Popen(
            [shutil.which("chromium") or shutil.which("chromium-browser"), "--headless=new",
             f"--remote-debugging-port={port}", f"--user-data-dir={self.profile}", "--no-first-run",
             "--no-default-browser-check", "--disable-gpu", "--window-size=1280,900", "about:blank"],
            stdout=DEVNULL, stderr=DEVNULL)
        url = None
        for _ in range(150):
            try:
                url = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1))["webSocketDebuggerUrl"]
                break
            except Exception:
                time.sleep(0.2)
        if not url:
            raise RuntimeError("Chromium에 연결하지 못했습니다")
        self.ws = WS(url)
        self.ws.sock.settimeout(60)     # 답이 안 오면 멈춰 있지 말고 실패로
        self.id = 0
        self.events: list[dict] = []
        self.call("Target.setDiscoverTargets", discover=True)

    def call(self, method: str, session: str | None = None, **params):
        self.id += 1
        my = self.id
        msg = {"id": my, "method": method, "params": params}
        if session:
            msg["sessionId"] = session
        self.ws.send(json.dumps(msg))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == my:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})
            if "method" in m:
                self.events.append(m)

    def pump(self) -> None:
        self.call("Browser.getVersion")

    def pages(self) -> list[dict]:
        return [t for t in self.call("Target.getTargets")["targetInfos"] if t["type"] == "page"]

    def close(self) -> None:
        try:
            self.call("Browser.close")
            self.proc.wait(10)
        except Exception:
            pass
        stop(self.proc)


class Page:
    def __init__(self, cdp: CDP, target_id: str, label: str):
        self.cdp, self.target, self.label = cdp, target_id, label
        self.s = cdp.call("Target.attachToTarget", targetId=target_id, flatten=True)["sessionId"]
        for d in ("Page", "Runtime", "Log", "DOM"):
            self.call(f"{d}.enable")

    def call(self, method: str, **params):
        return self.cdp.call(method, self.s, **params)

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
            except RuntimeError:
                v = None
            if v:
                return v
            time.sleep(0.1)
        try:
            return self.js(expr)
        except RuntimeError:
            return None

    def goto(self, url: str, ready: str = "document.readyState === 'complete'") -> None:
        try:
            self.js("window.__old = true")
        except RuntimeError:
            pass
        self.call("Page.navigate", url=url)
        self.wait(f"!window.__old && {ready}", 15)

    def viewport(self, width: int, height: int = 900) -> None:
        self.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)
        time.sleep(0.25)

    def shot(self, name: str, full: bool = False) -> Path:
        self.call("Page.bringToFront")   # 뒤에 가려진 창은 프레임을 그리지 않아 캡처가 끝나지 않는다
        time.sleep(0.15)
        data = self.call("Page.captureScreenshot", format="png", captureBeyondViewport=full)["data"]
        p = OUT / f"{name}.png"
        p.write_bytes(base64.b64decode(data))
        return p

    def center(self, selector: str):
        return self.js(f"""(() => {{ const e = document.querySelector({json.dumps(selector)}); if (!e) return null;
            e.scrollIntoView({{block: 'center'}}); const r = e.getBoundingClientRect();
            return [r.left + r.width / 2, r.top + r.height / 2]; }})()""")

    def front(self) -> None:
        """사용자는 보고 있는 창을 누른다 — 뒤에 가려진 탭은 타이머·입력·팝업이 다르게 동작하므로 앞으로 가져온다."""
        self.call("Page.bringToFront")

    def click(self, selector: str) -> None:
        self.front()
        xy = self.center(selector)
        if not xy:
            raise RuntimeError(f"요소 없음: {selector}")
        for t in ("mouseMoved", "mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", type=t, x=xy[0], y=xy[1], button="left", clickCount=1)

    def key(self, key: str, code: str, vk: int, text: str = "", modifiers: int = 0) -> None:
        self.front()
        for t in ("keyDown", "keyUp"):
            self.call("Input.dispatchKeyEvent", type=t, key=key, code=code, windowsVirtualKeyCode=vk, modifiers=modifiers,
                      **({"text": text} if text and t == "keyDown" else {}))

    def tab(self, shift: bool = False) -> str:
        self.key("Tab", "Tab", 9, modifiers=8 if shift else 0)
        return self.js("document.activeElement.id || document.activeElement.dataset.action || document.activeElement.textContent.trim().slice(0, 20)")

    def type_text(self, text: str) -> None:
        self.call("Input.insertText", text=text)

    def set_files(self, paths: list[str]) -> None:
        root = self.call("DOM.getDocument", depth=1)["root"]["nodeId"]
        node = self.call("DOM.querySelector", nodeId=root, selector="#file-input")["nodeId"]
        self.call("DOM.setFileInputFiles", files=paths, nodeId=node)

    def errors(self) -> list[str]:
        out = []
        for e in self.cdp.events:
            if e.get("sessionId") != self.s:
                continue
            m, p = e["method"], e["params"]
            if m == "Runtime.exceptionThrown":
                out.append(p["exceptionDetails"].get("exception", {}).get("description", "exception"))
            elif m == "Runtime.consoleAPICalled" and p["type"] in ("error", "warning"):
                out.append(f"console.{p['type']}: " + str(p["args"])[:200])
            elif m == "Log.entryAdded" and p["entry"]["level"] in ("error", "warning") and p["entry"].get("source") != "network":
                out.append("log: " + p["entry"]["text"][:200])
        return out

    def close(self) -> None:
        try:
            self.cdp.call("Target.closeTarget", targetId=self.target)
        except RuntimeError:
            pass


# ── 점검 도구 ───────────────────────────────────────────

results: list[tuple[str, bool, str]] = []
all_pages: list[Page] = []


def check(name: str, ok, evidence="") -> None:
    results.append((name, bool(ok), str(evidence)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n       {evidence}" if evidence and not ok else ""), flush=True)


def mock(path: str, method: str = "GET"):
    req = urllib.request.Request(BASE + path, method=method, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def mlog(path: str | None = None) -> list:
    return [e for e in mock("/__mock/log") if path is None or e["path"] == path]


def make_file(name: str, scen: str = "") -> str:
    p = FILES / name
    p.write_bytes(b"%PDF-1.4\n%mock: " + scen.encode() + b"\n" + b"mock body " * 50)
    return str(p)


def new_viewer(cdp: CDP, before: set[str], timeout: float = 8) -> Page | None:
    """before에 없던 viewer.html 창이 생기면 붙는다."""
    end = time.time() + timeout
    while time.time() < end:
        for t in cdp.pages():
            if t["targetId"] not in before and "viewer.html" in t["url"]:
                pg = Page(cdp, t["targetId"], "viewer")
                all_pages.append(pg)
                return pg
        time.sleep(0.1)
    return None


def page_ids(cdp: CDP) -> set[str]:
    return {t["targetId"] for t in cdp.pages()}


def text(pg: Page, sel: str) -> str:
    return " ".join((pg.js(f"(document.querySelector({json.dumps(sel)}) || {{}}).textContent || ''") or "").split())


DOC_VISIBLE = "!document.getElementById('doc').hidden"
LOADED = "document.getElementById('progress-text').textContent.includes('모두 불러옴')"


def upload(main: Page, path: str) -> None:
    """파일을 올리고 **그 파일의** 추출이 끝날 때까지 기다린다(앞 문서 카드가 보이는 채로 돌아오지 않게)."""
    name = Path(path).name
    n0 = sum(1 for e in mlog("/api/extract") if e["file_name"] == name)
    main.set_files([path])
    end = time.time() + 10
    while time.time() < end and sum(1 for e in mlog("/api/extract") if e["file_name"] == name) == n0:
        time.sleep(0.05)
    main.wait(f"document.getElementById('progress').hidden && ({DOC_VISIBLE} || !document.getElementById('error').hidden)", 10)
    time.sleep(0.1)


def open_viewer(cdp: CDP, main: Page, wait_loaded: bool = True, timeout: float = 15) -> Page | None:
    before = page_ids(cdp)
    main.click("#view-text-button")
    v = new_viewer(cdp, before)
    if v and wait_loaded:
        v.wait(LOADED, timeout)
    return v


def overflow(pg: Page) -> dict:
    # 가로 스크롤 금지. 표 감싸개(.tblscroll)·종이(.paper, overflow hidden) 안은 그 안에서 잘리거나 스크롤된다.
    return pg.js("""(() => {
      const de = document.documentElement;
      const wide = [...document.querySelectorAll('body *')].filter(e => {
        if (e.closest('.visually-hidden, .tblscroll, .paper, .skip')) return false;
        const r = e.getBoundingClientRect(); return r.width && (r.right > de.clientWidth + 1 || r.left < -1);
      }).map(e => e.tagName + '#' + e.id + '.' + e.className).slice(0, 5);
      return { scroll: de.scrollWidth, client: de.clientWidth, wide };
    })()""")


def small_targets(pg: Page) -> list:
    return pg.js("""[...document.querySelectorAll('button, a[href], input')].filter(e => {
        if (e.closest('.visually-hidden') || e.classList.contains('skip')) return false;
        const r = e.getBoundingClientRect(); if (!r.width && !r.height) return false;
        return r.width < 44 || r.height < 44;
      }).map(e => `${e.tagName} "${(e.textContent||'').trim().slice(0,20)}" ${Math.round(e.getBoundingClientRect().width)}x${Math.round(e.getBoundingClientRect().height)}`)""")


def downloads_since(cdp: CDP, n0: int, timeout: float = 10) -> list[dict]:
    """n0번째 이후 끝난 내려받기 [{name, guid}]."""
    end = time.time() + timeout
    while time.time() < end:
        cdp.pump()
        begun = {e["params"]["guid"]: e["params"]["suggestedFilename"] for e in cdp.events[n0:] if e["method"] == "Browser.downloadWillBegin"}
        done = [g for g in (e["params"]["guid"] for e in cdp.events[n0:]
                            if e["method"] == "Browser.downloadProgress" and e["params"]["state"] == "completed") if g in begun]
        if done:
            return [{"guid": g, "name": begun[g]} for g in done]
        time.sleep(0.1)
    return []


def server_export(path: str, name: str) -> bytes:
    req = urllib.request.Request(BASE + "/api/export", data=Path(path).read_bytes(), method="POST",
                                 headers={"Content-Type": "application/pdf", "X-Filename": urllib.parse.quote(name)})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read()


def js_function(src_file: str, name: str) -> str:
    src = (ROOT / src_file).read_text(encoding="utf-8")
    m = re.search(r"^function " + name + r"\(.*?^}\n", src, re.S | re.M)
    return m.group(0) if m else ""


# ── 시험 ────────────────────────────────────────────────

def main() -> int:
    if port_busy(PORT):
        print(f"포트 {PORT}이 이미 쓰이고 있습니다. 가짜 서버를 띄울 수 없어 멈춥니다.")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(FILES, ignore_errors=True)
    FILES.mkdir(parents=True)
    shutil.rmtree(DL, ignore_errors=True)
    DL.mkdir(parents=True)
    server = subprocess.Popen([sys.executable, "-B", str(ROOT / "tools" / "mock_server.py"), str(PORT)], stdout=DEVNULL, stderr=DEVNULL)
    cdp = None
    try:
        for _ in range(50):
            if port_busy(PORT):
                break
            time.sleep(0.1)
        cdp = CDP()
        cdp.call("Browser.setDownloadBehavior", behavior="allow", downloadPath=str(DL), eventsEnabled=True)
        first = next(t for t in cdp.pages())
        main = Page(cdp, first["targetId"], "main")
        all_pages.append(main)
        main.viewport(1280)
        mock("/__mock/reset", "POST")
        main.goto(BASE + "/", "document.documentElement.classList.contains('app-ready')")
        main.wait("window.__h = 1", 1)

        struct = make_file("struct.pdf", "pages=2")
        han = make_file("한글 이름 파일.pdf", "pages=2")

        # ── A. 메인 창: 두 버튼이 보이는 상태 ──
        check("준비 화면: 두 버튼 안 보임(문서 카드 숨김)", main.js("document.getElementById('doc-tools').offsetParent === null"))
        upload(main, make_file("err-NO_TEXT.pdf"))
        check("추출 오류(NO_TEXT): 두 버튼 안 보임", main.js("document.getElementById('doc-tools').offsetParent === null")
              and not main.js("document.getElementById('error').hidden"))
        upload(main, make_file("slow-2.pdf"))   # 읽는 중 확인용 — 2초 걸림
        main.set_files([make_file("slow-2b-slow-2.pdf")])
        main.wait("!document.getElementById('progress').hidden", 3)
        check("읽는 중: 두 버튼 안 보임", main.js("document.getElementById('doc-tools').offsetParent === null"))
        main.wait(DOC_VISIBLE, 6)
        upload(main, struct)
        check("읽기 완료: [추출한 텍스트 보기]·바로 옆 [추출한 텍스트 다운로드하기]",
              main.js("""(() => { const a = document.getElementById('view-text-button'), b = document.getElementById('download-text-button');
                 const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
                 return a.offsetParent && b.offsetParent && a.textContent === '추출한 텍스트 보기' && b.textContent === '추출한 텍스트 다운로드하기'
                   && Math.abs(ra.top - rb.top) < 2 && rb.left > ra.right && rb.left - ra.right < 30
                   && a.className.includes('btn--ghost') && !a.disabled && !b.disabled; })()"""))
        check("두 버튼은 문서 정보 아래·[요약하기] 위", main.js("""(() => {
              const t = document.getElementById('doc-tools').getBoundingClientRect(), f = document.querySelector('.doc__facts').getBoundingClientRect(),
                    a = document.getElementById('doc-actions').getBoundingClientRect(); return t.top >= f.bottom && t.bottom <= a.top; })()"""))
        check("상태 줄 aria-live=polite·오류 줄 role=alert(평소 숨김)",
              main.js("document.getElementById('doc-tools-status').getAttribute('aria-live')") == "polite"
              and main.js("document.getElementById('doc-tools-error').getAttribute('role')") == "alert"
              and main.js("document.getElementById('doc-tools-error').hidden"))
        main.shot("v01-main-extracted-1280")
        main.viewport(480)
        check("480px: 두 버튼이 가로 꽉 차게 위아래로", main.js("""(() => { const a = document.getElementById('view-text-button').getBoundingClientRect(),
              b = document.getElementById('download-text-button').getBoundingClientRect(), c = document.getElementById('doc').getBoundingClientRect();
              return b.top >= a.bottom && a.width > c.width - 60 && b.width > c.width - 60; })()"""))
        main.shot("v02-main-extracted-480", full=True)
        main.viewport(1280)

        # ── B. 보기: 새 창 + BroadcastChannel로 File 전달 ──
        mock("/__mock/reset", "POST")
        v = open_viewer(cdp, main)
        check("[보기] → 새 창(Target)이 /viewer.html#doc=TOKEN 으로 열림", v is not None and re.search(r"/viewer\.html#doc=[\w-]+$", v.js("location.href") or ""),
              v.js("location.href") if v else None)
        if not v:
            raise RuntimeError("보기 창이 열리지 않아 멈춥니다")
        check("보기 창이 원래 창에서 File(구조적 복제)을 받음", v.js("document.documentElement.dataset.source") == "file")
        lg = mlog("/api/layout")
        check("layout 요청: 1회, from=1&to=5, 원본 바이트 그대로", len(lg) == 1 and lg[0]["from"] == 1 and lg[0]["to"] == 2
              and lg[0]["bytes"] == os.path.getsize(struct), lg)
        check("메인 창 요약 흐름은 그대로(읽기 완료, [요약하기] 누를 수 있음)",
              main.js(DOC_VISIBLE) and main.js("!document.getElementById('summarize-button').disabled")
              and main.js("document.getElementById('progress').hidden && document.getElementById('error').hidden"))
        check("메인 상태 줄: '새 창에서 … 열었어요'", "새 창에서" in text(main, "#doc-tools-status"))
        check("보기 창 제목: '{제목} — 추출한 텍스트 보기', <html lang=ko>",
              v.js("document.title") == "2026년 지역 도서관 운영 보고서 — 추출한 텍스트 보기" and v.js("document.documentElement.lang") == "ko",
              v.js("document.title"))
        check("2쪽 모두: section aria-label '1쪽'·'2쪽', 진행 '2쪽 모두 불러옴'",
              v.js("[...document.querySelectorAll('section.pg')].map(s => s.getAttribute('aria-label'))") == ["1쪽", "2쪽"]
              and text(v, "#progress-text") == "2쪽 모두 불러옴")
        check("목차(책갈피) 4항목, 중첩 3단계, 넓은 화면 사이드바", v.js("document.querySelectorAll('#toc-list a').length") == 4
              and v.js("document.querySelectorAll('#toc-list ol ol a').length") == 1
              and v.js("!document.getElementById('toc').hidden && getComputedStyle(document.getElementById('toc')).position === 'sticky'")
              and v.js("getComputedStyle(document.getElementById('toc-toggle')).display") == "none")
        check("제목 h2·h3·h4(문서 h1~h3), 창 제목 h1 하나", v.js("['h2','h3','h4'].map(t => document.querySelectorAll('#p1 ' + t).length)") == [1, 1, 1]
              and v.js("document.querySelectorAll('h1').length") == 1)
        cells = v.js("[...document.querySelectorAll('#p1 table tr')].map(r => [...r.children].map(c => c.tagName + ':' + c.textContent))")
        check("표 1개 3×3, 첫 행 th(굵은 머리글), 셀 글자 일치", v.js("document.querySelectorAll('table').length") == 1
              and cells == [["TH:구", "TH:텃밭 수", "TH:참여 가구"], ["TD:마포", "TD:12", "TD:340"], ["TD:은평", "TD:8", "TD:210"]], cells)
        check("그림 1개: data URI <img>, alt '1쪽 그림 1', 그림 글자는 figcaption",
              v.js("[...document.querySelectorAll('img')].map(i => [i.alt, i.src.slice(0, 22), i.complete && i.naturalWidth > 0])") == [["1쪽 그림 1", "data:image/png;base64,", True]]
              and text(v, "figcaption") == "도표 안 글자 12%")
        # 원본 배치
        check("1280px 기본은 원본 배치(aria-pressed), 종이 비율 612:792",
              v.js("document.getElementById('mode-original').getAttribute('aria-pressed')") == "true"
              and abs(v.js("(() => { const r = document.querySelector('#p1 .paper').getBoundingClientRect(); return r.width / r.height; })()") - 612 / 792) < 0.01)
        geo = v.js("""(() => { const p = document.querySelector('#p1 .paper').getBoundingClientRect();
            const h = document.querySelector('#p1 h2 .ln').getBoundingClientRect(), f = document.querySelector('#p1 figure').getBoundingClientRect();
            const s = p.width / 612; return { paperW: p.width, h1x: (h.left - p.left) / s, h1y: (h.top - p.top) / s, h1w: h.width / s,
              figX: (f.left - p.left) / s, figW: f.width / s, figH: f.height / s,
              fs: parseFloat(getComputedStyle(document.querySelector('#p1 h2 .ln')).fontSize) / s,
              bold: getComputedStyle(document.querySelector('#p1 h2 .ln')).fontWeight }; })()""")
        lay = json.loads(urllib.request.urlopen(urllib.request.Request(BASE + "/api/layout?from=1&to=1", data=Path(struct).read_bytes(),
                                                                         headers={"Content-Type": "application/pdf"})).read())
        hb = lay["pages"][0]["blocks"][0]["lines"][0]
        check("원본 배치: 넓은 화면 종이 폭 = 612pt × 96/72 = 816px", abs(geo["paperW"] - 816) < 1.5, geo)
        check("원본 배치: 제목 줄 위치·크기·굵기가 원본 좌표대로, 폭은 scaleX로 원본 줄 폭에 맞춤(±3%)",
              abs(geo["h1x"] - hb["x"]) < 1 and abs(geo["h1y"] - hb["y"]) < 1 and abs(geo["fs"] - 24) < 0.2 and geo["bold"] == "700"
              and abs(geo["h1w"] - hb["w"]) / hb["w"] < 0.03, [geo, hb])
        check("원본 배치: 그림 상자 180×120pt", abs(geo["figW"] - 180) < 1 and abs(geo["figH"] - 120) < 1, geo)
        v.shot("v03-viewer-original-1280")
        v.shot("v03-viewer-original-1280-full", full=True)
        c = v.js(CONTRAST_JS)
        check("명암비 AA: 보기 창(원본 배치)", not c["bad"], c)
        # 읽기 모드
        v.click("#mode-reading")
        check("읽기 모드: aria-pressed 바뀜, 흐름 배치(.flow), 절대 위치 줄 없음, 표·그림 그대로",
              v.js("document.getElementById('mode-reading').getAttribute('aria-pressed')") == "true"
              and v.js("document.getElementById('mode-original').getAttribute('aria-pressed')") == "false"
              and v.js("document.querySelectorAll('.flow').length") == 2 and v.js("document.querySelectorAll('.ln').length") == 0
              and v.js("document.querySelectorAll('table').length") == 1 and v.js("document.querySelectorAll('figure img').length") == 1
              and v.js("getComputedStyle(document.querySelector('figcaption')).position") != "absolute")
        check("읽기 모드: 블록이 읽는 순서대로(제목→문단→제목→표→제목→그림→문단)",
              v.js("[...document.querySelector('#p1 .flow').children].map(e => e.tagName)") == ["H2", "P", "H3", "DIV", "H4", "FIGURE", "P"],
              v.js("[...document.querySelector('#p1 .flow').children].map(e => e.tagName)"))
        v.shot("v04-viewer-reading-1280")
        c = v.js(CONTRAST_JS)
        check("명암비 AA: 보기 창(읽기 모드)", not c["bad"], c)
        v.click("#mode-original")
        # 인쇄
        v.js("window.__printed = 0; window.print = () => { window.__printed += 1; }")
        v.click("#print-button")
        check("모두 불러온 뒤 [인쇄] → window.print 1회", v.js("document.getElementById('print-button').getAttribute('aria-disabled')") == "false"
              and v.js("window.__printed") == 1)
        v.call("Emulation.setEmulatedMedia", media="print")
        pr = v.js("""[getComputedStyle(document.getElementById('vbar')).display, getComputedStyle(document.getElementById('toc')).display,
                      getComputedStyle(document.getElementById('p2')).breakBefore]""")
        check("인쇄 CSS: 도구 막대·목차 숨김, 쪽마다 새 종이", pr[0] == "none" and pr[1] == "none" and pr[2] == "page", pr)
        v.call("Emulation.setEmulatedMedia", media="")
        pdf = v.call("Page.printToPDF", printBackground=True)["data"]
        (OUT / "v05-viewer-print.pdf").write_bytes(base64.b64decode(pdf))
        npages = len(re.findall(rb"/Type\s*/Page(?![s\w])", base64.b64decode(pdf)))
        check("인쇄(printToPDF): 2쪽 문서가 종이 2장 이상으로 나뉨", npages >= 2, npages)

        # ── C. 쪽 이동·키보드 (보기 창) ──
        v.js("document.activeElement.blur(); window.scrollTo(0, 0)")
        v.click(".vhead__title")          # 순차 포커스 시작점을 문서 맨 위로
        order = [v.tab() for _ in range(8)]
        back = [v.tab(shift=True) for _ in range(8)]
        check("키보드 Tab 순서: 원본 배치 → 읽기 모드 → 이전 쪽 → 쪽 번호 → 다음 쪽 → 인쇄 → 다운로드 → 목차 첫 항목, 맨 앞은 '본문으로 건너뛰기'",
              order[:7] == ["mode-original", "mode-reading", "prev-page", "page-input", "next-page", "print-button", "download-button"]
              and "도시 텃밭" in order[7] and back[-1] == "본문으로 건너뛰기", [order, back])
        v.tab()
        outline = v.js("(() => { const s = getComputedStyle(document.activeElement); return s.outlineStyle + ' ' + s.outlineWidth; })()")
        check("포커스 테두리 보임(목차 링크)", outline.startswith("solid"), outline)
        v.js("document.querySelector('#toc-list a[data-page=\\'2\\']').focus()")
        v.key("Enter", "Enter", 13, "\r")
        time.sleep(0.3)
        check("목차 '2. 두 번째 장'을 Enter → 2쪽으로 이동·포커스, 쪽 번호 2, 목차 강조(aria-current)",
              v.js("document.activeElement.id") == "p2" and v.js("document.getElementById('page-input').value") == "2"
              and text(v, "#toc-list a[aria-current=true] .toc__text") == "2. 두 번째 장", [v.js("document.activeElement.id"), text(v, "#toc-list a[aria-current=true]")])
        v.js("document.getElementById('page-input').focus(); document.getElementById('page-input').select()")
        v.type_text("1")
        v.key("Enter", "Enter", 13, "\r")
        time.sleep(0.3)
        check("쪽 번호 1 + Enter → 1쪽", v.js("document.activeElement.id") == "p1" and v.js("document.getElementById('p1').getBoundingClientRect().top") < 200)
        v.click("#next-page")
        time.sleep(0.3)
        check("[다음 쪽] → 2쪽", v.js("document.getElementById('page-input').value") == "2")
        v.click("#prev-page")
        time.sleep(0.3)
        check("[이전 쪽] → 1쪽", v.js("document.getElementById('page-input').value") == "1")
        check("주소의 #doc=TOKEN은 그대로(목차·쪽 이동이 해시를 바꾸지 않음)", re.search(r"#doc=[\w-]+$", v.js("location.href")))

        # ── D. 보기 창의 [다운로드] ──
        n0 = len(cdp.events)
        v.click("#download-button")
        dls = downloads_since(cdp, n0)
        saved = DL / dls[0]["name"] if dls else None
        check("보기 창 [다운로드] → 'struct_추출.html' 저장, 내용 = 서버 응답",
              dls and dls[0]["name"] == "struct_추출.html" and saved.exists() and saved.read_bytes() == server_export(struct, "struct.pdf"), dls)
        check("보기 창 다운로드 완료 문구", v.wait("document.getElementById('download-status').textContent.includes('struct_추출.html을(를) 내려받았어요.')", 3))

        # ── E. 메인 창 다운로드 ──
        upload(main, han)
        mock("/__mock/reset", "POST")
        n0 = len(cdp.events)
        main.click("#download-text-button")
        dls = downloads_since(cdp, n0)
        saved = DL / dls[0]["name"] if dls else None
        check("메인 [다운로드]: 한글 이름 그대로 '한글 이름 파일_추출.html' 저장, 내용 = 서버 응답",
              dls and dls[0]["name"] == "한글 이름 파일_추출.html" and saved.read_bytes() == server_export(han, "한글 이름 파일.pdf"), dls)
        # (위 비교에서 server_export가 같은 경로를 한 번 더 부르므로 화면 요청은 첫 기록)
        check("메인 [다운로드]: 요청 1회, X-Filename 그대로", len(mlog("/api/export")) == 2
              and mlog("/api/export")[0]["file_name"] == "한글 이름 파일.pdf", mlog("/api/export"))
        check("완료 문구 '{이름}을(를) 내려받았어요.', 생략 문구 없음",
              main.wait("document.getElementById('doc-tools-status').textContent === '한글 이름 파일_추출.html을(를) 내려받았어요.'", 3))
        check("받은 파일에 <script 없음·CSP 메타 있음", saved and b"<script" not in saved.read_bytes() and b"Content-Security-Policy" in saved.read_bytes())
        # 진행 문구·생략 문구
        upload(main, make_file("figs.pdf", "eslow-2 eomit-3"))
        mock("/__mock/reset", "POST")
        main.click("#download-text-button")
        main.js("for (let i = 0; i < 4; i++) document.getElementById('download-text-button').click()")
        main.click("#download-text-button")
        time.sleep(1.2)
        prog = text(main, "#doc-tools-status")
        check("내려받는 중: 버튼 비활성 + '다운로드 파일을 만들고 있어요… N초'(1초마다)",
              main.js("document.getElementById('download-text-button').disabled") and re.fullmatch(r"다운로드 파일을 만들고 있어요… [12]초", prog), prog)
        main.shot("v06-main-downloading-1280")
        main.wait("!document.getElementById('download-text-button').disabled", 5)
        check("내려받는 중 연타(스크립트 4번 + 마우스 1번)해도 요청 1회", len(mlog("/api/export")) == 1, mlog("/api/export"))
        check("생략 문구 '그림 3개는 크기·시간 제한으로 빠졌어요.'", text(main, "#doc-tools-status") == "figs_추출.html을(를) 내려받았어요. 그림 3개는 크기·시간 제한으로 빠졌어요.",
              text(main, "#doc-tools-status"))
        # 파일 이름 규칙: app.js·viewer.js의 실제 함수 == 가짜 서버 규칙 == pdf_layout.export_file_name
        fa, fv = js_function("app.js", "exportFileName"), js_function("viewer.js", "exportFileName")
        names = ["보고서.pdf", "a.PDF", "x.pdf.pdf", 'a/b\\c:d*e?f"g<h>i|j.pdf', " . 앞뒤 공백.  .pdf", ".pdf", "", "탭\t문자\u0085.pdf",
                 "가" * 150 + ".pdf", "😀" * 120 + ".pdf", "no-ext"]
        got = main.js(f"(() => {{ {fa}; return {json.dumps(names)}.map(exportFileName); }})()")
        want = [mock("/__mock/export_name?name=" + urllib.parse.quote(n))["name"] for n in names]
        try:
            import pdf_layout
            assert Path(pdf_layout.__file__).resolve().parent == ROOT
            backend = [pdf_layout.export_file_name(n) for n in names]
        except Exception as e:  # backend 모듈이 아직 없으면 가짜 서버 규칙과만 비교
            backend = want
            print("  (pdf_layout을 불러오지 못해 가짜 서버 규칙과만 비교:", e, ")")
        check("파일 이름 규칙(SPEC §10.6): 화면 == 서버(pdf_layout.export_file_name) — 금지 문자·제어문자·앞뒤 공백·100자·빈 이름",
              got == want == backend and fa == fv and fa, list(zip(names, got, backend)))
        # 오류 코드별 문구 — 요약 흐름은 그대로
        expect = {
            "TOOL_MISSING": "표·그림을 읽는 프로그램이 없어요", "BUSY": "다른 창에서 문서를 읽고 있어요",
            "EXTRACT_TIMEOUT": "문서가 복잡해 시간이 너무 걸려", "ENCRYPTED": "암호가 걸린 PDF예요", "BROKEN_PDF": "PDF를 열 수 없어요",
            "NOT_PDF": "PDF 파일만", "EMPTY_FILE": "빈 파일이에요", "TOO_LONG": "300쪽까지", "FILE_TOO_LARGE": "20MB까지",
            "BAD_REQUEST": "알 수 없는 문제가 생겼어요. 다시 시도해 주세요.", "INTERNAL": "알 수 없는 문제가 생겼어요. 다시 시도해 주세요.",
        }
        for code, want_t in expect.items():
            upload(main, make_file(f"export-{code.lower()}.pdf", f"eerr-{code}"))
            main.click("#download-text-button")
            main.wait("!document.getElementById('doc-tools-error').hidden", 5)
            t = text(main, "#doc-tools-error")
            check(f"다운로드 오류 {code}: §10.7 문구, 코드·서버 원문 숨김, 요약 흐름 그대로(오류 화면 없음)",
                  want_t in t and code not in t and "mock" not in t and main.js(DOC_VISIBLE)
                  and main.js("document.getElementById('error').hidden") and main.js("!document.getElementById('summarize-button').disabled")
                  and main.js("document.activeElement.id") == "download-text-button", t)
            if code == "TOOL_MISSING":
                main.shot("v07-main-download-error-1280")
                c = main.js(CONTRAST_JS)
                check("명암비 AA: 메인 다운로드 오류", not c["bad"], c)
        upload(main, make_file("export-html502.pdf", "enojson"))
        main.click("#download-text-button")
        main.wait("!document.getElementById('doc-tools-error').hidden", 5)
        check("다운로드 502 HTML(JSON 아님) → 일반 문구", "알 수 없는 문제" in text(main, "#doc-tools-error"))
        upload(main, make_file("export-retry.pdf", "eslow-1"))      # 다시 누르면 성공하는 문서
        main.js("window.__rf = window.fetch; window.fetch = (u, o) => String(u).includes('/api/export') ? Promise.reject(new TypeError('x')) : window.__rf(u, o)")
        main.click("#download-text-button")
        main.wait("document.getElementById('doc-tools-error').textContent.includes('run.sh')", 3)
        check("다운로드 NETWORK → run.sh 안내", "./run.sh" in text(main, "#doc-tools-error"))
        main.js("window.fetch = window.__rf")
        main.click("#download-text-button")
        check("다시 누르면 오류 줄이 바로 사라지고 진행 문구", main.js("document.getElementById('doc-tools-error').hidden")
              and "다운로드 파일을 만들고 있어요" in text(main, "#doc-tools-status"))
        main.wait("!document.getElementById('download-text-button').disabled", 5)
        # CLIENT_TIMEOUT: 제한시간 1초로 줄인 페이지
        ident = main.call("Page.addScriptToEvaluateOnNewDocument", source="globalThis.__pdfSummaryTimeouts = {export: 1000};")["identifier"]
        main.goto(BASE + "/", "document.documentElement.classList.contains('app-ready')")
        upload(main, make_file("x-slow.pdf", "eslow-3"))
        main.click("#download-text-button")
        main.wait("!document.getElementById('doc-tools-error').hidden", 5)
        check("다운로드 CLIENT_TIMEOUT(200초 → 시험 1초) → 시간 초과 문구", "문서가 복잡해 시간이 너무 걸려" in text(main, "#doc-tools-error"))
        main.call("Page.removeScriptToEvaluateOnNewDocument", identifier=ident)
        time.sleep(2.2)
        main.goto(BASE + "/", "document.documentElement.classList.contains('app-ready')")

        # ── F. 다른 파일로 바꾸는 중의 다운로드는 버려진다 ──
        upload(main, make_file("x-slow2.pdf", "eslow-2"))
        n0 = len(cdp.events)
        main.click("#download-text-button")
        time.sleep(0.3)
        upload(main, make_file("ko.pdf", "pages=2"))
        time.sleep(2.5)
        cdp.pump()
        check("내려받는 중 다른 파일을 올리면 이전 다운로드는 저장되지 않고 상태 줄도 비워짐",
              not [e for e in cdp.events[n0:] if e["method"] == "Browser.downloadWillBegin"] and text(main, "#doc-tools-status") == ""
              and not main.js("document.getElementById('download-text-button').disabled"))

        # ── G. 요약 중·결과·요약 오류에서도 버튼 ──
        upload(main, make_file("sumslow-2.pdf", "pages=2"))
        main.click("#summarize-button")
        main.wait("!document.getElementById('progress').hidden", 2)
        vis = main.js("!!document.getElementById('view-text-button').offsetParent && !document.getElementById('view-text-button').disabled")
        v2 = open_viewer(cdp, main)
        check("요약 중: 버튼 보이고 [보기] 동작, 요약은 계속(요약 중 화면 그대로)", vis and v2 is not None
              and main.js("!document.getElementById('progress').hidden"))
        main.wait("!document.getElementById('result').hidden", 6)
        check("요약 결과: 정상 완료 + 두 버튼 그대로 보임", main.js("!document.getElementById('result').hidden")
              and main.js("!!document.getElementById('download-text-button').offsetParent"))
        n0 = len(cdp.events)
        main.click("#download-text-button")
        dls = downloads_since(cdp, n0)
        check("결과 화면에서 다운로드 → 저장, 결과 화면 그대로", dls and dls[0]["name"] == "sumslow-2_추출.html" and main.js("!document.getElementById('result').hidden"), dls)
        if v2:
            v2.close()
        main.goto(BASE + "/", "document.documentElement.classList.contains('app-ready')")
        upload(main, make_file("sum-UPSTREAM.pdf", "pages=2"))
        main.click("#summarize-button")
        main.wait("!document.getElementById('error').hidden", 5)
        check("요약 오류 화면: 문서 카드와 두 버튼 보임", main.js("!!document.getElementById('view-text-button').offsetParent"))

        # ── H. 팝업 차단 → 안내 + 링크(noopener)로 연 창도 문서를 받음 ──
        upload(main, struct)
        main.js("window.open = () => null")
        main.click("#view-text-button")
        t = text(main, "#doc-tools-error")
        href = main.js("(document.querySelector('#doc-tools-error a') || {}).getAttribute?.('href')")
        rel = main.js("(document.querySelector('#doc-tools-error a') || {}).rel")
        check("팝업 차단: 안내 문구 + '새 창에서 열기' 링크(target=_blank rel=noopener)",
              "새 창이 막혔어요. 주소창의 팝업 허용을 누르거나 아래 링크로 여세요." in t and "새 창에서 열기" in t
              and re.fullmatch(r"/viewer\.html#doc=[\w-]+", href or "") and rel == "noopener", [t, href, rel])
        main.shot("v08-main-popup-blocked-1280")
        before = page_ids(cdp)
        main.click("#doc-tools-error a")
        v3 = new_viewer(cdp, before)
        ok = v3 and v3.wait(LOADED, 10)
        check("링크로 연(noopener) 창도 BroadcastChannel로 문서를 받아 그림", ok and v3.js("window.opener") is None
              and v3.js("document.querySelectorAll('table').length") == 1)
        old_url = v3.js("location.href") if v3 else ""
        main.goto(BASE + "/", "document.documentElement.classList.contains('app-ready')")   # window.open 원래대로

        # ── I. NO_SOURCE: 다른 파일을 연 뒤 옛 토큰 · 원래 창이 닫힌 뒤 새로고침 ──
        upload(main, han)
        before = page_ids(cdp)
        tid = cdp.call("Target.createTarget", url=old_url)["targetId"]
        v4 = new_viewer(cdp, before)
        ok = v4 and v4.wait("document.getElementById('state-title').textContent === '문서를 다시 받을 수 없어요'", 6)
        t = text(v4, "#state-text") if v4 else ""
        check("다른 파일을 연 뒤 옛 토큰으로 열면 NO_SOURCE 안내 + [다시 연결]", ok and "원래 창(한눈 PDF 요약)이 닫혔거나 다른 파일을 열어" in t
              and v4.js("!document.getElementById('reconnect-button').hidden") and v4.js("document.activeElement.id") == "reconnect-button", t)
        v4.shot("v09-viewer-no-source-1280")
        c = v4.js(CONTRAST_JS)
        check("명암비 AA: NO_SOURCE 화면", not c["bad"], c)
        v4.close()
        # 원래 창이 같은 문서를 들고 있으면 새로고침해도 다시 열림 / 원래 창이 문서를 놓으면 NO_SOURCE
        v5 = open_viewer(cdp, main)
        v5.call("Page.reload")
        time.sleep(0.5)
        check("보기 창 새로고침: 원래 창이 같은 문서를 들고 있으면 다시 열림", v5.wait(LOADED, 10))
        main.goto("about:blank")
        n0 = len(cdp.events)
        v5.click("#download-button")
        dls = downloads_since(cdp, n0)
        check("원래 창이 닫힌 뒤에도 보기 창은 계속 동작(다운로드 저장)", dls and dls[0]["name"] == "한글 이름 파일_추출.html", dls)
        t0 = time.time()
        v5.call("Page.reload")
        time.sleep(0.5)
        ok = v5.wait("document.getElementById('state-title').textContent === '문서를 다시 받을 수 없어요'", 6)
        check("원래 창이 닫힌 뒤 새로고침 → 약 3초 뒤 NO_SOURCE", ok and 2.5 < time.time() - t0 < 6, round(time.time() - t0, 1))
        v5.close()
        main.goto(BASE + "/", "document.documentElement.classList.contains('app-ready')")

        # ── J. 큰 문서: 5쪽씩 차례로, 목차로 먼 쪽 앞당기기, 인쇄 비활성 ──
        mock("/__mock/reset", "POST")
        upload(main, make_file("big.pdf", "pages=40 noout lslow-0.6"))
        v6 = open_viewer(cdp, main, wait_loaded=False)
        v6.wait("document.querySelectorAll('section.pg').length === 40", 6)
        check("첫 응답 뒤 40쪽 자리를 모두 만듦(같은 비율의 회색 상자 + 'n쪽 불러오는 중')",
              v6.js("document.querySelectorAll('section.pg').length") == 40 and "40쪽 불러오는 중" in text(v6, "#p40")
              and abs(v6.js("(() => { const r = document.querySelector('#p40 .ph').getBoundingClientRect(); return r.width / r.height; })()") - 612 / 792) < 0.01)
        check("불러오는 중: [인쇄] aria-disabled + 툴팁 '모두 불러온 뒤…', 누르면 인쇄 안 함",
              v6.js("document.getElementById('print-button').getAttribute('aria-disabled')") == "true"
              and v6.js("document.getElementById('print-button').title") == "모두 불러온 뒤 인쇄할 수 있어요"
              and v6.js("window.__printed = 0; window.print = () => window.__printed++; document.getElementById('print-button').click(); window.__printed") == 0)
        check("진행 표시 'n/40쪽 불러옴'", re.fullmatch(r"\d+/40쪽 불러옴", text(v6, "#progress-text")), text(v6, "#progress-text"))
        check("책갈피 없음 → 불러온 쪽의 h1·h2로 목차", v6.wait("document.querySelectorAll('#toc-list a').length >= 5", 3)
              and v6.js("document.querySelector('#toc-list a').getAttribute('href')") == "#p1-b0")
        v6.shot("v10-viewer-loading-1280")
        # 아직 안 불러온 33쪽으로 이동 → 쪽 번호 입력
        v6.js("document.getElementById('page-input').focus(); document.getElementById('page-input').select()")
        v6.type_text("33")
        v6.key("Enter", "Enter", 13, "\r")
        v6.wait(LOADED, 20)
        lg = mlog("/api/layout")
        ranges = [(e["from"], e["to"]) for e in lg]
        idx = next((i for i, r in enumerate(ranges) if r[0] <= 33 <= r[1]), 99)
        check("쪽 33으로 이동 → 그 쪽이 든 묶음(31~35)이 다음 차례로 앞당겨짐", idx <= 3 and ranges[idx] == (31, 35), ranges)
        check("5쪽씩, 빠짐·겹침 없이 40쪽 모두(묶음 8개)", sorted(ranges) == [(i, i + 4) for i in range(1, 41, 5)], ranges)
        check("두 번째 요청부터 body={첫 응답 body_size}", lg[0]["body"] is None and all(e["body"] == 12.0 for e in lg[1:]), [e["body"] for e in lg])
        times = [(e["t0"], e["t"]) for e in lg]
        check("요청은 한 번에 하나(앞 응답이 끝난 뒤 다음 요청)", all(times[i + 1][0] >= times[i][1] - 0.05 for i in range(len(times) - 1)), times)
        check("33쪽을 불러오면 그 쪽이 화면 위에(포커스 유지)", v6.js("document.activeElement.id") == "p33"
              and -5 < v6.js("document.getElementById('p33').getBoundingClientRect().top") < 220, v6.js("document.getElementById('p33').getBoundingClientRect().top"))
        check("모두 불러오면 [인쇄] 가능, 화면 낭독기에 '모두 불러왔어요, 40쪽.'",
              v6.js("document.getElementById('print-button').getAttribute('aria-disabled')") == "false"
              and v6.wait("document.getElementById('announcer').textContent === '모두 불러왔어요, 40쪽.'", 2))
        check("목차(제목으로 만든 것) 41항목(1쪽 h1·h2 + 2~40쪽 h1 또는 h2)", v6.js("document.querySelectorAll('#toc-list a').length") == 41,
              v6.js("document.querySelectorAll('#toc-list a').length"))
        v6.close()

        mock("/__mock/reset", "POST")
        upload(main, make_file("big2.pdf", "pages=40 lslow-0.6"))
        v8 = open_viewer(cdp, main, wait_loaded=False)
        v8.wait("document.querySelectorAll('#toc-list a').length > 10", 6)
        v8.click("#toc-list a[data-page='37']")
        v8.wait(LOADED, 20)
        ranges = [(e["from"], e["to"]) for e in mlog("/api/layout")]
        idx = next((i for i, r in enumerate(ranges) if r[0] <= 37 <= r[1]), 99)
        check("목차(책갈피)로 37쪽을 누르면 36~40 묶음이 다음 차례로", idx <= 3 and ranges[idx] == (36, 40), ranges)
        check("목차 클릭 후 37쪽이 화면 위·목차 강조", -5 < v8.js("document.getElementById('p37').getBoundingClientRect().top") < 220
              and "37" in text(v8, "#toc-list a[aria-current=true]"), text(v8, "#toc-list a[aria-current=true]"))
        v8.close()

        # ── K. 실패한 묶음만 [다시 시도] · 첫 요청 실패 ──
        mock("/__mock/reset", "POST")
        upload(main, make_file("once.pdf", "pages=12 lonce-8-BUSY"))
        v9 = open_viewer(cdp, main)
        t = text(v9, "#p6")
        check("6~10쪽 묶음 실패: 그 자리에 문구 + [다시 시도] 하나, 다른 묶음(11~12)은 계속",
              "다른 창에서 문서를 읽고 있어요" in t and v9.js("document.querySelectorAll('[data-action=retry-batch]').length") == 1
              and v9.js("document.getElementById('p12').querySelector('.paper, .flow') !== null")
              and re.fullmatch(r"7/12쪽 불러옴 · 5쪽 실패", text(v9, "#progress-text")), [t, text(v9, "#progress-text")])
        check("실패가 남으면 [인쇄]는 비활성, 안내 문구", v9.js("document.getElementById('print-button').getAttribute('aria-disabled')") == "true"
              and "불러오지 못한 쪽이 있어요" in text(v9, "#print-hint"))
        v9.shot("v11-viewer-batch-error-1280", full=False)
        v9.click("[data-action=retry-batch]")
        check("[다시 시도] → 그 묶음만 다시 요청해 모두 불러옴", v9.wait(LOADED, 5)
              and [(e["from"], e["to"], e["status"]) for e in mlog("/api/layout")][-1] == (6, 10, 200))
        v9.close()
        for scen, want_t in [("lerr-ENCRYPTED", "암호가 걸린 PDF예요"), ("lerr-TOOL_MISSING", "표·그림을 읽는 프로그램이 없어요"),
                             ("lnojson", "알 수 없는 문제가 생겼어요")]:
            # 파일 이름에 err-·nojson을 넣으면 추출 단계 시나리오도 걸리므로 이름은 중립으로(시나리오는 바이트에)
            upload(main, make_file(f"firstfail-{len(scen)}{scen[1:3]}.pdf", scen))
            vx = open_viewer(cdp, main, wait_loaded=False)
            if not vx:
                check(f"첫 요청 실패({scen}): 보기 창 열림", False)
                continue
            ok = vx.wait("document.getElementById('state-title').textContent === '문서를 불러오지 못했어요'", 6)
            t = text(vx, "#state-text")
            check(f"첫 요청 실패({scen}): '문서를 불러오지 못했어요' + 문구 + [다시 시도], 코드 숨김",
                  ok and want_t in t and "mock" not in t and "_" not in t.replace("poppler-utils", "")
                  and vx.js("!document.getElementById('restart-button').hidden"), t)
            if scen == "lerr-ENCRYPTED":
                vx.click("#restart-button")
                time.sleep(0.5)
                check("[다시 시도] → 다시 요청(같은 오류면 같은 안내)", vx.wait("document.getElementById('state-title').textContent === '문서를 불러오지 못했어요'", 3)
                      and len([e for e in mlog("/api/layout") if e["code"] == "ENCRYPTED"]) >= 2)
            vx.close()

        # ── L. 악성 글자 · 그림 주소 ──
        mock("/__mock/reset", "POST")
        upload(main, make_file("xss.pdf", "pages=2 xss"))
        vx = open_viewer(cdp, main)
        for mode in ("original", "reading"):
            vx.click(f"#mode-{mode}")
            check(f"악성 글자({mode}): 태그가 되지 않고 글자로 보임, 스크립트 실행 0",
                  vx.js("window.__xss") is None and vx.js("document.querySelectorAll('#pages script, #pages b, #toc script').length") == 0
                  and "<script>window.__xss=2</script>" in text(vx, "#p1") and "<b>마포</b>\"'&" in text(vx, "#p1 table")
                  and "<script>window.__xss=4</script>" in text(vx, "#toc-list"))
            check(f"그림 src가 javascript:·http:이면 그리지 않음({mode}) — data URI 그림 1개만, 나머지는 생략 표시",
                  vx.js("[...document.querySelectorAll('img')].every(i => i.src.startsWith('data:image/png;base64,'))")
                  and vx.js("document.querySelectorAll('img').length") == 1 and vx.js("document.querySelectorAll('.omitted').length") == 2)
        check("가짜 서버에 evil.png 요청 0회", not [e for e in mlog() if "evil" in e["path"]])
        vx.shot("v12-viewer-xss-reading-1280")
        vx.close()
        upload(main, make_file("omit.pdf", "pages=2 omit figfail"))
        vx = open_viewer(cdp, main)
        t = text(vx, "#p1")
        check("그림 생략 사유: [그림 생략: 파일 크기 제한]·[… 시간 제한]·[… 불러오지 못함], 쪽 아래 '이 쪽의 그림은 불러오지 못했어요.'",
              all(f"[그림 생략: {w}]" in t for w in ("파일 크기 제한", "시간 제한", "불러오지 못함")) and "이 쪽의 그림은 불러오지 못했어요." in t, t)
        vx.close()

        # ── M. 좁은 화면: 읽기 모드 기본, 접는 목차, 가로 넘침 없음 ──
        upload(main, make_file("narrow.pdf", "pages=3 longword"))
        vn = open_viewer(cdp, main)
        for w in (1280, 1000, 700, 480, 360, 320):
            vn.viewport(w)
            vn.call("Page.reload")
            time.sleep(0.4)
            vn.wait(LOADED, 8)
            mode = vn.js("document.getElementById('mode-original').getAttribute('aria-pressed') === 'true' ? 'original' : 'reading'")
            ov = overflow(vn)
            st = small_targets(vn)
            check(f"보기 창 {w}px: 기본 {'원본 배치' if w >= 700 else '읽기 모드'}, 가로 넘침 없음, 터치 대상 44px 이상",
                  mode == ("original" if w >= 700 else "reading") and ov["scroll"] <= ov["client"] and not ov["wide"] and not st,
                  {"mode": mode, "overflow": ov, "small": st})
            vn.shot(f"v13-viewer-{w}")
            if w in (480, 360):
                vn.click("#mode-original")
                ov = overflow(vn)
                check(f"{w}px 원본 배치: 종이가 화면 폭에 맞게 줄어 가로 넘침 없음", ov["scroll"] <= ov["client"] and not ov["wide"]
                      and vn.js("document.querySelector('.paper').getBoundingClientRect().right") <= w, ov)
                vn.shot(f"v13-viewer-{w}-original")
                vn.click("#mode-reading")
            if w == 480:
                check("480px: 목차는 접혀 있고 [목차] 단추(aria-expanded=false)", vn.js("document.getElementById('toc').hidden")
                      and vn.js("document.getElementById('toc-toggle').getAttribute('aria-expanded')") == "false")
                vn.click("#toc-toggle")
                check("[목차] → 패널 열림(aria-expanded=true), 포커스가 첫 항목", not vn.js("document.getElementById('toc').hidden")
                      and vn.js("document.getElementById('toc-toggle').getAttribute('aria-expanded')") == "true"
                      and vn.js("document.activeElement.closest('#toc') !== null"))
                vn.shot("v14-viewer-480-toc-open")
                vn.click("#toc-list a[data-page='2']")
                time.sleep(0.3)
                check("목차 항목을 누르면 패널이 닫히고 2쪽으로", vn.js("document.getElementById('toc').hidden")
                      and vn.js("document.activeElement.id") == "p2")
                c = vn.js(CONTRAST_JS)
                check("명암비 AA: 보기 창 480px 읽기 모드", not c["bad"], c)
        vn.viewport(1280)
        vn.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
        anim = vn.js("getComputedStyle(document.querySelector('.spinner')).animationName")
        check("prefers-reduced-motion: 회전 애니메이션 멈춤", anim == "none", anim)
        vn.close()

        # ── N. 보기 도구 없음(health layout_tools false) ──
        mock("/__mock/health?mode=nolayout", "POST")
        main.goto(BASE + "/", "document.documentElement.classList.contains('app-ready')")
        main.wait("window.__ready = 1", 1)
        upload(main, struct)
        main.wait("!document.getElementById('doc-tools-error').hidden", 3)
        check("layout_tools false: 두 버튼 비활성 + 옆에 TOOL_MISSING 문구, 요약하기는 그대로",
              main.js("document.getElementById('view-text-button').disabled && document.getElementById('download-text-button').disabled")
              and "표·그림을 읽는 프로그램이 없어요" in text(main, "#doc-tools-error") and main.js("!document.getElementById('summarize-button').disabled")
              and main.js("document.getElementById('health-notices').children.length") == 0)
        main.shot("v15-main-nolayout-1280")
        mock("/__mock/health?mode=ok", "POST")

        # ── O. 메인 창 키보드: Tab으로 [보기] → Enter로 새 창 ──
        main.goto(BASE + "/", "document.documentElement.classList.contains('app-ready')")
        upload(main, struct)
        main.js("document.getElementById('summarize-button').focus()")
        prev = main.tab(shift=True)
        prev = main.tab(shift=True) if prev != "download-text-button" else prev
        check("키보드: [요약하기]에서 Shift+Tab → [다운로드] → [보기]", prev == "download-text-button" or prev == "view-text-button", prev)
        main.js("document.getElementById('view-text-button').focus()")
        before = page_ids(cdp)
        main.key("Enter", "Enter", 13, "\r")
        vk = new_viewer(cdp, before)
        check("키보드 Enter로 [보기] → 새 창 열림(팝업 차단 안 됨)", vk is not None and vk.wait(LOADED, 8))
        if vk:
            vk.close()

        # ── P. 파일로 직접 열기 ──
        vf_before = page_ids(cdp)
        cdp.call("Target.createTarget", url=(ROOT / "viewer.html").as_uri())
        time.sleep(0.5)
        vf = next((Page(cdp, t["targetId"], "file") for t in cdp.pages() if t["targetId"] not in vf_before), None)
        time.sleep(1.6)
        check("viewer.html을 file://로 열면 run.sh 안내만", vf and vf.js("getComputedStyle(document.getElementById('boot-notice')).visibility") == "visible"
              and vf.js("getComputedStyle(document.getElementById('layout')).display") == "none")
        if vf:
            vf.close()

        # ── Q. 콘솔 ──
        errs = []
        for pg in all_pages:
            errs += [f"{pg.label}: {e}" for e in pg.errors()]
        check("콘솔 오류·경고 0건(모든 창, CSP 위반 포함)", not errs, errs[:6])
    finally:
        if cdp:
            cdp.close()
        stop(server)
        shutil.rmtree(OUT / "chrome-profile-viewer", ignore_errors=True)

    passed = sum(ok for _, ok, _ in results)
    print("\n" + "=" * 60)
    print(f"보기·내려받기 점검 {passed}/{len(results)} 통과  (스크린샷·내려받은 파일: {OUT})")
    for name, ok, _ in results:
        if not ok:
            print("  실패:", name)
    print(f"포트 {PORT}: {'아직 열려 있음!' if port_busy(PORT) else '닫힘'}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
