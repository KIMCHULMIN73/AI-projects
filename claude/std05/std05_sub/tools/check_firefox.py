#!/usr/bin/env python3
"""Firefox에서 업로드 → 요약 → 결과 → 복사 (SPEC §9.3 "Chromium·Firefox 두 브라우저"). 개발 전용. 모델 호출 0회.
(4단계) 이어서 [추출한 텍스트 보기] 새 창(BroadcastChannel로 File 전달)·두 모드·목차, 보기 창과 메인 창의 [다운로드](내려받기 폴더는
프로필 user.js로 OUT/ff-downloads에 고정), 받은 .html을 Firefox로 열어 표·그림·목차·스크립트 0, xss.pdf 실행 0을 본다.

    python3 -B tools/check_firefox.py --out 저장소_밖_폴더 [--pdfs DIR]

geckodriver·selenium이 없어 Firefox에 내장된 WebDriver BiDi(--remote-debugging-port, 웹소켓)를 직접 쓴다.
실제 server.py(tools/e2e_app.py, 8030) + 가짜 업스트림(tools/fake_upstream.py, 8033). 끝나면 모두 끈다.
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

import check_e2e as e2e  # noqa: E402
from check_frontend import WS  # noqa: E402


class BiDi:
    def __init__(self, profile: Path):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.proc = subprocess.Popen([shutil.which("firefox"), "--headless", "--no-remote", "--profile", str(profile),
                                      f"--remote-debugging-port={port}", "about:blank"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        end = time.time() + 40
        while True:
            try:
                self.ws = WS(f"ws://127.0.0.1:{port}/session")
                break
            except OSError:
                if time.time() > end:
                    raise
                time.sleep(0.3)
        self.id = 0
        self.call("session.new", capabilities={"alwaysMatch": {"unhandledPromptBehavior": {"default": "dismiss"}}})
        self.ctx = self.call("browsingContext.getTree")["contexts"][0]["context"]
        self.logs: list = []
        self.call("session.subscribe", events=["log.entryAdded"])

    def call(self, method: str, **params):
        self.id += 1
        my = self.id
        self.ws.send(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("type") == "event":
                if m.get("method") == "log.entryAdded":
                    self.logs.append(m["params"])
                continue
            if m.get("id") == my:
                if m.get("type") == "error":
                    raise RuntimeError(f"{method}: {m.get('error')} {m.get('message')}")
                return m.get("result", {})

    def js(self, expr: str):
        r = self.call("script.evaluate", expression=expr, target={"context": self.ctx}, awaitPromise=True)
        if r.get("type") == "exception":
            raise RuntimeError(r["exceptionDetails"].get("text"))
        v = r["result"]
        return v.get("value") if v.get("type") not in ("undefined", "null") else None

    def wait(self, expr: str, timeout: float = 10):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.js(expr):
                    return True
            except RuntimeError:
                pass
            time.sleep(0.1)
        return False

    def node(self, selector: str) -> dict:
        r = self.call("script.evaluate", expression=f"document.querySelector({json.dumps(selector)})",
                      target={"context": self.ctx}, awaitPromise=False, resultOwnership="root")
        return {"sharedId": r["result"]["sharedId"]}

    def click(self, selector: str) -> None:
        """실제 포인터 입력(사용자 동작으로 인정 — 클립보드 쓰기가 허용된다)."""
        self.js(f"document.querySelector({json.dumps(selector)}).scrollIntoView({{block: 'center'}})")
        el = self.node(selector)
        self.call("input.performActions", context=self.ctx, actions=[{
            "type": "pointer", "id": "mouse", "parameters": {"pointerType": "mouse"},
            "actions": [{"type": "pointerMove", "x": 0, "y": 0, "origin": {"type": "element", "element": el}},
                        {"type": "pointerDown", "button": 0}, {"type": "pointerUp", "button": 0}]}])

    def contexts(self) -> list[dict]:
        return self.call("browsingContext.getTree")["contexts"]

    def wait_context(self, needle: str, before: set, timeout: float = 10) -> str | None:
        end = time.time() + timeout
        while time.time() < end:
            for c in self.contexts():
                if c["context"] not in before and needle in c.get("url", ""):
                    return c["context"]
            time.sleep(0.1)
        return None

    def close(self) -> None:
        try:
            self.call("browser.close")
        except Exception:  # noqa: BLE001
            pass
        e2e.stop(self.proc)


VIEW_JS = """JSON.stringify({toc: document.querySelectorAll('#toc-list a').length, tables: document.querySelectorAll('#pages table').length,
  imgs: [...document.querySelectorAll('#pages img')].filter(i => i.complete && i.naturalWidth > 0).length,
  pages: document.querySelectorAll('section.pg').length, source: document.documentElement.dataset.source || '',
  scripts: document.querySelectorAll('#pages script').length, xss: window.__xss ?? null,
  text: document.getElementById('page-list').textContent.replace(/\\s+/g, ' ').slice(0, 3000)})"""
FILE_JS = """JSON.stringify({tables: document.querySelectorAll('table').length, toc: document.querySelectorAll('nav a').length,
  imgs: [...document.querySelectorAll('img')].filter(i => i.complete && i.naturalWidth > 0).length,
  scripts: document.querySelectorAll('script').length, xss: window.__xss ?? null,
  res: performance.getEntriesByType('resource').length, text: document.body.textContent.slice(0, 3000)})"""
LOADED = "document.getElementById('progress-text').textContent.includes('모두 불러옴')"


def wait_file(dl: Path, name: str, timeout: float = 20) -> Path | None:
    end = time.time() + timeout
    while time.time() < end:
        f = dl / name
        if f.is_file() and f.stat().st_size > 0 and not (dl / (name + ".part")).exists():
            time.sleep(0.3)
            return f
        time.sleep(0.2)
    return None


def viewer_flow(b: BiDi, P: dict, dl: Path, out: Path) -> None:
    """(4단계) 보기 창·내려받기 — Firefox."""
    main = b.ctx
    b.call("browsingContext.setViewport", context=main, viewport={"width": 1280, "height": 900})
    b.call("browsingContext.navigate", context=main, url=e2e.BASE, wait="complete")
    b.wait(e2e.READY, 5)
    b.call("input.setFiles", context=main, element=b.node("#file-input"), files=[str(P["struct.pdf"])])
    b.wait(e2e.V["doc"] + " && document.getElementById('doc-file').textContent === 'struct.pdf'", 10)
    before = {c["context"] for c in b.contexts()}
    b.click("#view-text-button")
    vctx = b.wait_context("viewer.html", before)
    info = {}
    if vctx:
        b.ctx = vctx
        b.wait(LOADED, 20)
        info = json.loads(b.js(VIEW_JS))
    e2e.check("Firefox 보기: 새 창이 열리고 File을 받아(BroadcastChannel) 2쪽·목차 4·표 1·그림 1",
              vctx and info.get("source") == "file" and info.get("pages") == 2 and info.get("toc") == 4
              and info.get("tables") == 1 and info.get("imgs") == 1, info)
    if not vctx:
        return
    b.click("#mode-reading")
    r = json.loads(b.js(VIEW_JS))
    e2e.check("Firefox 보기: [읽기 모드]에서도 표·그림 유지", r["tables"] == 1 and r["imgs"] == 1, r)
    import base64
    (out / "firefox-viewer-1280-reading.png").write_bytes(base64.b64decode(
        b.call("browsingContext.captureScreenshot", context=vctx)["data"]))
    b.click("#download-button")
    f = wait_file(dl, "struct_추출.html")
    e2e.check("Firefox 보기 창 [다운로드] → struct_추출.html 저장·완료 문구", f is not None
              and "내려받았어요" in (b.js("document.getElementById('download-status').textContent") or ""), sorted(p.name for p in dl.iterdir()))
    b.ctx = main
    b.call("input.setFiles", context=main, element=b.node("#file-input"), files=[str(P["xss.pdf"])])
    b.wait(e2e.V["doc"] + " && document.getElementById('doc-file').textContent === 'xss.pdf'", 10)
    b.click("#download-text-button")
    fx = wait_file(dl, "xss_추출.html")
    before = {c["context"] for c in b.contexts()}
    b.click("#view-text-button")
    x = b.wait_context("viewer.html", before)
    xi = {}
    if x:
        b.ctx = x
        b.wait(LOADED, 20)
        xi = json.loads(b.js(VIEW_JS))
    e2e.check("Firefox xss.pdf: 메인 창 [다운로드] 저장(연속 두 번째 파일), 보기 창은 글자 그대로·스크립트 0·실행 0",
              fx is not None and xi.get("scripts") == 0 and xi.get("xss") is None and "<script>alert(1)</script>" in xi.get("text", ""),
              (fx, {k: xi.get(k) for k in ("scripts", "xss")}))
    for path, want_tables in ((f, 1), (fx, 1)):
        if path is None:
            continue
        b.ctx = main
        b.call("browsingContext.navigate", context=main, url=path.as_uri(), wait="complete")
        time.sleep(0.5)
        fi = json.loads(b.js(FILE_JS))
        e2e.check(f"Firefox로 받은 {path.name} 열기: 표 {want_tables}·스크립트 0·외부 자원 요청 0·실행 0"
                  + ("·그림 1·목차 4" if path is f else "·글자 그대로"),
                  fi["tables"] == want_tables and fi["scripts"] == 0 and fi["res"] == 0 and fi["xss"] is None
                  and ((fi["imgs"] == 1 and fi["toc"] == 4) if path is f else "<script>alert(1)</script>" in fi["text"]),
                  {k: fi[k] for k in fi if k != "text"})
    errs = [l for l in b.logs if l.get("level") == "error"]
    e2e.check("Firefox 보기·내려받기: 콘솔 오류 0건", not errs, errs[:3])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--pdfs")
    a = ap.parse_args()
    out = Path(a.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    for p in (e2e.PORT, e2e.UP_PORT):
        if e2e.port_busy(p):
            print(f"포트 {p}이 쓰이고 있습니다.")
            return 2
    P = e2e.ensure_pdfs(Path(a.pdfs) if a.pdfs else out / "pdfs")
    upstream = subprocess.Popen([sys.executable, "-B", str(TOOLS / "fake_upstream.py"), str(e2e.UP_PORT)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    app = e2e.App(out)
    profile = Path(tempfile.mkdtemp(prefix="ff-profile-", dir=out))
    dl = out / "ff-downloads"
    shutil.rmtree(dl, ignore_errors=True)
    dl.mkdir()
    (profile / "user.js").write_text("".join(f'user_pref("{k}", {json.dumps(v)});\n' for k, v in {
        "browser.download.folderList": 2, "browser.download.dir": str(dl), "browser.download.useDownloadDir": True,
        "browser.download.always_ask_before_handling_new_types": False, "browser.download.manager.showWhenStarting": False,
        "browser.download.alwaysOpenPanel": False, "browser.download.start_downloads_in_tmp_dir": False}.items()))
    b = None
    try:
        e2e.wait_port(e2e.UP_PORT, True, 5)
        app.start()
        b = BiDi(profile)
        b.call("browsingContext.navigate", context=b.ctx, url=e2e.BASE, wait="complete")
        e2e.check("Firefox: 첫 화면(app.js 모듈 실행, 업로드 영역)", b.wait(e2e.READY, 5) and b.js(e2e.V["upload"]))
        b.call("input.setFiles", context=b.ctx, element=b.node("#file-input"), files=[str(P["ko.pdf"])])
        e2e.check("Firefox: 파일 선택 → 읽기 완료(문서 카드)", b.wait(e2e.V["doc"], 8)
                  and b.js("document.getElementById('doc-title').textContent") == e2e.mk.KO_TITLE)
        b.click("#summarize-button")
        e2e.check("Firefox: [요약하기] → 결과", b.wait(e2e.V["result"], 10)
                  and "호출 1회" in (b.js("document.getElementById('result-meta').textContent") or ""))
        b.click("#copy-button")
        b.wait("document.getElementById('copy-status').textContent", 3)
        status = b.js("document.getElementById('copy-status').textContent")
        e2e.check("Firefox: [결과 복사] → '복사했어요'", status == "복사했어요", status)
        b.call("browsingContext.navigate", context=b.ctx, url=e2e.BASE, wait="complete")
        b.wait(e2e.READY, 5)
        b.call("input.setFiles", context=b.ctx, element=b.node("#file-input"), files=[str(P["scan.pdf"])])
        b.wait(e2e.V["error"], 8)
        e2e.check("Firefox: 스캔본 → NO_TEXT 문구", e2e.EXPECT["NO_TEXT"] in (b.js("document.getElementById('error-text').textContent") or ""))
        b.call("browsingContext.setViewport", context=b.ctx, viewport={"width": 480, "height": 900})
        ov = json.loads(b.js("JSON.stringify([document.documentElement.scrollWidth, document.documentElement.clientWidth])"))
        e2e.check("Firefox 480px: 가로 넘침 없음", ov[0] <= ov[1], ov)
        shot = b.call("browsingContext.captureScreenshot", context=b.ctx)["data"]
        import base64
        (out / "firefox-480-error.png").write_bytes(base64.b64decode(shot))
        viewer_flow(b, P, dl, out)
        b.ctx = b.contexts()[0]["context"]
        b.call("browsingContext.navigate", context=b.ctx, url=(e2e.APP_DIR / "index.html").as_uri(), wait="complete")
        time.sleep(1.6)
        e2e.check("Firefox: file://로 열면 실행 안내", b.js("getComputedStyle(document.getElementById('boot-notice')).visibility") == "visible")
        errs = [l for l in b.logs if l.get("level") == "error" and "favicon" not in str(l)]
        e2e.check("Firefox: 콘솔 오류 0건(file:// 모듈 차단 제외)", not [l for l in errs if "file://" not in str(l)], errs[:3])
    finally:
        if b:
            b.close()
        app.stop()
        e2e.stop(upstream)
        shutil.rmtree(profile, ignore_errors=True)
    passed = sum(ok for _, ok, _ in e2e.results)
    print(f"\nFirefox 점검 {passed}/{len(e2e.results)} 통과 · 실제 모델 호출 0회")
    return 0 if passed == len(e2e.results) else 1


if __name__ == "__main__":
    sys.exit(main())
