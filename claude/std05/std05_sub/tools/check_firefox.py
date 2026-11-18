#!/usr/bin/env python3
"""Firefox에서 업로드 → 요약 → 결과 → 복사 (SPEC §9.3 "Chromium·Firefox 두 브라우저"). 개발 전용. 모델 호출 0회.

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

    def close(self) -> None:
        try:
            self.call("browser.close")
        except Exception:  # noqa: BLE001
            pass
        e2e.stop(self.proc)


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
