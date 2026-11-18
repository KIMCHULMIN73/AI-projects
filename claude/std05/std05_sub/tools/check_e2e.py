#!/usr/bin/env python3
"""한눈 PDF 요약 — 통합 시험(실제 server.py + 실제 화면). 개발 전용(앱은 이 파일을 로드하지 않는다).

    python3 -B tools/check_e2e.py --out 저장소_밖_폴더                    # 가짜 업스트림, 실제 모델 호출 0회
    python3 -B tools/check_e2e.py --out 저장소_밖_폴더 --live              # run.sh 실제 실행 + 실제 모델(ko.pdf 1건)
    python3 -B tools/check_e2e.py --out 저장소_밖_폴더 --live --docs ko.pdf,en.pdf

기본 모드:
  - tools/fake_upstream.py(127.0.0.1:8033)를 띄우고, tools/e2e_app.py로 **실제 server.py**를 8030에 띄운다
    (llm.API_URL만 가짜로, 키는 가짜 키 — 진짜 키는 어디로도 나가지 않는다).
  - headless Chromium(CDP, tools/check_frontend.py의 Browser 재사용)으로 tools/make_test_pdfs.py의 PDF를
    파일 선택·드래그 앤 드롭으로 올려 추출 → 요약 → 결과 → 복사 → 다른 파일까지, 오류·장애·취소·보안·화면 크기를 본다.
  - 마지막에 run.sh 자체(이미 떠 있음·포트 점유·Ctrl+C·다이어리 8020과 동시 실행)를 가상 터미널로 시험한다.
--live 모드: `NO_OPEN=1 ./run.sh`를 가상 터미널로 실제 실행(진짜 키)하고 --docs의 문서를 화면에서 실제로 요약한다.
  하루 한도(DAILY_LIMIT)를 만나면 멈춘다. 쓴 호출 수(결과의 '호출 N회')를 합쳐 찍는다.

8030·8033(기본), 8030(--live)이 비어 있어야 한다. 시험 PDF·스크린샷·로그는 --out 아래에 둔다(저장소 밖).
끝나면 띄운 서버·브라우저를 모두 끈다.
"""

from __future__ import annotations

import argparse
import json
import os
import pty
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
APP_DIR = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import check_frontend as cf  # noqa: E402  (Browser·overflow·EXPECT 재사용 — 같은 tools/ 안)
import make_test_pdfs as mk  # noqa: E402
from fake_upstream import FAKE_KEY  # noqa: E402

PORT = 8030
UP_PORT = 8033
BASE = f"http://127.0.0.1:{PORT}/"
UP = f"http://127.0.0.1:{UP_PORT}"
V = cf.V
READY = cf.READY
EXPECT = cf.EXPECT

results: list[tuple[str, bool, str]] = []


def check(name: str, ok, evidence="") -> bool:
    results.append((name, bool(ok), str(evidence)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n       {str(evidence)[:400]}" if evidence and not ok else ""), flush=True)
    return bool(ok)


def port_busy(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_port(port: int, up: bool, timeout: float = 8) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if port_busy(port) == up:
            return True
        time.sleep(0.1)
    return port_busy(port) == up


def http(method: str, url: str, body: bytes | None = None, headers: dict | None = None, timeout: float = 10):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def up_ctl(path: str, obj: dict | None = None):
    st, _, raw = http("POST" if obj is not None else "GET", UP + path,
                      json.dumps(obj).encode() if obj is not None else None, {"Content-Type": "application/json"})
    return json.loads(raw)


def up_script(script: list, default: dict | None = None) -> None:
    up_ctl("/__ctl/reset", {})
    up_ctl("/__ctl/script", {"script": script, "default": default})


def up_log() -> list:
    return up_ctl("/__ctl/log")


def stop(proc) -> None:
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()


# ── 서버 프로세스 ──────────────────────────────────────────

class App:
    """tools/e2e_app.py(= 실제 server.py, llm.API_URL만 가짜)를 띄우고 끈다. 로그는 파일로."""

    def __init__(self, out: Path):
        self.out = out
        self.proc = None
        self.log_path = out / "server.log"
        self.log_path.write_text("")

    def start(self, *, key: bool = True, path_env: str | None = None, home: str | None = None) -> None:
        env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
        if key:
            env["OPENROUTER_API_KEY"] = FAKE_KEY
        if path_env is not None:
            env["PATH"] = path_env
        if home is not None:
            env["HOME"] = home
        log = open(self.log_path, "a")
        self.proc = subprocess.Popen([sys.executable, "-B", str(TOOLS / "e2e_app.py"),
                                      f"{UP}/api/v1/chat/completions", "6", "14"],
                                     cwd=str(APP_DIR), env=env, stdout=log, stderr=log)
        if not wait_port(PORT, True, 8):
            raise RuntimeError("server.py가 뜨지 않았다: " + self.log_path.read_text()[-500:])

    def stop(self) -> None:
        stop(self.proc)
        self.proc = None
        wait_port(PORT, False, 5)

    def log(self) -> str:
        return self.log_path.read_text(errors="replace")

    def count(self, needle: str) -> int:
        return self.log().count(needle)


# ── 가상 터미널로 run.sh ──────────────────────────────────

class Term:
    """bash ./run.sh를 가상 터미널에서 실행한다(Ctrl+C = 터미널에 \\x03 → 앞쪽 프로세스 그룹에 SIGINT)."""

    def __init__(self, cwd: Path, env: dict):
        self.buf = b""
        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(cwd)
            os.execve("/bin/bash", ["bash", "./run.sh"], env)
        self.pid, self.fd = pid, fd
        self.status = None

    def read(self, timeout: float = 0.2) -> str:
        end = time.time() + timeout
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [], max(0.0, end - time.time()))
            if not r:
                break
            try:
                chunk = os.read(self.fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            self.buf += chunk
        return self.buf.decode("utf-8", "replace")

    def wait_text(self, text: str, timeout: float = 10) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if text in self.read(0.2):
                return True
            if self.poll() is not None:
                return text in self.read(0.2)
        return False

    def poll(self):
        if self.status is None:
            pid, st = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.status = os.waitstatus_to_exitcode(st)
        return self.status

    def wait(self, timeout: float = 10):
        end = time.time() + timeout
        while time.time() < end and self.poll() is None:
            self.read(0.1)
        return self.poll()

    def ctrl_c(self) -> None:
        os.write(self.fd, b"\x03")

    def kill(self) -> None:
        if self.poll() is None:
            try:
                os.killpg(self.pid, signal.SIGTERM)
            except OSError:
                pass
            self.wait(5)
        try:
            os.close(self.fd)
        except OSError:
            pass


def runsh_env(**extra) -> dict:
    env = dict(os.environ)
    env.pop("OPENROUTER_API_KEY", None)   # 진짜 키는 config.py가 ~/.config/openrouter/env에서 읽는다
    env["NO_OPEN"] = "1"
    env.update(extra)
    return env


def pids_matching(word: str) -> list[int]:
    out = []
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
            except OSError:
                continue
            if word in cmd and str(os.getpid()) != d.name:
                out.append(int(d.name))
    return out


# ── 화면 도우미 ───────────────────────────────────────────

def text_of(b, id_: str) -> str:
    return " ".join((b.js(f"document.getElementById('{id_}').textContent") or "").split())


def fresh(b) -> None:
    b.goto(BASE, READY)


def upload(b, path: Path, timeout: float = 15) -> float:
    t0 = time.monotonic()
    b.set_files([str(path)])
    b.wait(f"!({V['progress']}) && ({V['doc']} || {V['error']})", timeout)
    return time.monotonic() - t0


def summarize(b, timeout: float = 20) -> float:
    t0 = time.monotonic()
    b.click("#summarize-button")
    b.wait(f"!({V['progress']}) && ({V['result']} || {V['error']})", timeout)
    return time.monotonic() - t0


def drag_files(b, paths: list[str]) -> None:
    xy = b.center("#drop-zone")
    data = {"items": [], "files": paths, "dragOperationsMask": 1}
    for t in ("dragEnter", "dragOver", "drop"):
        b.call("Input.dispatchDragEvent", type=t, x=xy[0], y=xy[1], data=data)


def hidden(b, id_: str) -> bool:
    return bool(b.js(f"document.getElementById('{id_}').hidden"))


def response_bodies(b) -> list[str]:
    """지금까지 받은 모든 응답 본문(가능한 것만) + 현재 페이지 소스."""
    out = [b.js("document.documentElement.outerHTML") or ""]
    ids = {e["params"]["requestId"] for e in b.events if e["method"] == "Network.responseReceived"}
    for rid in ids:
        try:
            r = b.call("Network.getResponseBody", requestId=rid)
            out.append(r.get("body", ""))
        except RuntimeError:
            pass
    return out


def files_with(root: Path, needle: bytes, since: float, skip: Path) -> list[str]:
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        if Path(dirpath).resolve().is_relative_to(skip.resolve()):
            dirnames[:] = []
            continue
        for fn in filenames:
            p = Path(dirpath) / fn
            try:
                st = p.stat()
                if st.st_mtime < since or st.st_size > 5_000_000 or not p.is_file():
                    continue
                if needle in p.read_bytes():
                    hits.append(str(p))
            except OSError:
                continue
    return hits


def ensure_pdfs(pdfs: Path) -> dict[str, Path]:
    pdfs.mkdir(parents=True, exist_ok=True)
    c = mk.Ctx(pdfs)
    try:
        for name in mk.CASES:
            if not (pdfs / name).is_file():
                mk.make(c, name)
        for name in mk.REAL_CASES:
            if not (pdfs / name).is_file():
                try:
                    mk.make(c, name)
                except Exception as exc:  # noqa: BLE001 — 원본 문서가 없는 기기
                    print(f"(건너뜀: {name} — {exc})")
        if not (pdfs / "xss_title.pdf").is_file():   # 제목에 HTML이 든 PDF(화면에서 글자로만 보여야 함)
            c.chromium(mk._page('<img src=x onerror="window.__xss=1">제목 <b>굵게</b>', mk._paras(mk.KO_PARAS)), "xss_title.pdf")
    finally:
        c.cleanup()
    (pdfs / "note.txt").write_text("PDF가 아닌 글 파일")
    return {p.name: p for p in pdfs.iterdir() if p.is_file()}


# ── 기본 모드: 가짜 업스트림 ──────────────────────────────

def run_fake(out: Path, P: dict[str, Path]) -> None:
    started = time.time()
    upstream = subprocess.Popen([sys.executable, "-B", str(TOOLS / "fake_upstream.py"), str(UP_PORT)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    app = App(out)
    b = None
    try:
        if not wait_port(UP_PORT, True, 5):
            raise RuntimeError("가짜 업스트림이 뜨지 않았다")
        app.start()
        cf.OUT = out
        b = cf.Browser()
        b.call("Page.setInterceptFileChooserDialog", enabled=True)
        b.call("Emulation.setTimezoneOverride", timezoneId="Asia/Seoul")
        b.call("Emulation.setScrollbarsHidden", hidden=True)   # 전체 페이지 캡처에서 스크롤바 폭만큼 잘리지 않게
        b.viewport(1280)
        fresh(b)

        # ── 1. 첫 화면 ──
        check("첫 화면: 실제 서버에서 app.js 뜸, /api/health 정상이라 미리 안내 없음, 업로드·외부 전송 안내 보임",
              b.js(READY) and b.wait("document.getElementById('health-notices').children.length === 0", 2)
              and not hidden(b, "upload") and "OpenRouter" in text_of(b, "privacy"))
        b.shot("e2e-01-ready-1280")

        # ── 2. 시험 PDF 전부(파일 선택) ──
        ok_cases = {"ko.pdf": None, "en.pdf": None, "multi.pdf": None, "mid.pdf": None, "restricted.pdf": None,
                    "inject.pdf": None, "한글 이름 파일.pdf": None, "mixed.pdf": "empty", "long.pdf": "trunc"}
        for name in ("real_xz_ko.pdf", "real_mkvmerge_ko.pdf", "real_mime_en.pdf"):
            if name in P:
                ok_cases[name] = None
        if "real_rpi_text.pdf" in P:
            ok_cases["real_rpi_text.pdf"] = "trunc"
        times = {}
        for name, note in ok_cases.items():
            fresh(b)
            dt = upload(b, P[name])
            times[name] = dt
            meta = json.loads(subprocess.run([sys.executable, "-B", "-c",
                                              "import sys,json;sys.path.insert(0,sys.argv[1]);import pdf_extract;"
                                              "i=pdf_extract.extract(open(sys.argv[2],'rb').read());i.pop('text');print(json.dumps(i))",
                                              str(APP_DIR), str(P[name])], capture_output=True, text=True).stdout)
            want_title = meta["title"] or name
            okk = (not hidden(b, "doc") and hidden(b, "error") and text_of(b, "doc-title") == want_title
                   and text_of(b, "doc-pages") == f"{meta['pages']:,}쪽"
                   and not b.js("document.getElementById('summarize-button').disabled"))
            if note == "trunc":
                okk = okk and f"앞 {meta['pages_used']:,}쪽" in text_of(b, "doc-truncated") and not hidden(b, "doc-truncated")
            else:
                okk = okk and hidden(b, "doc-truncated")
            if note == "empty":
                okk = okk and "글자가 없는 쪽 1개" in text_of(b, "doc-empty-pages")
            check(f"추출 {name}: 문서 카드(제목·{meta['pages']}쪽){' + 잘림 안내' if note == 'trunc' else ''}"
                  f"{' + 빈 쪽 안내' if note == 'empty' else ''}, [요약하기] 활성 ({dt:.2f}s)", okk,
                  [text_of(b, i) for i in ("doc-title", "doc-pages", "doc-chars", "doc-truncated", "doc-empty-pages", "error-text")])
            if name == "long.pdf":
                b.shot("e2e-02-long-extracted-1280")
                b.viewport(480); b.shot("e2e-02-long-extracted-480"); b.viewport(360)
                ov = cf.overflow(b)
                check("잘린 긴 문서 카드 360px: 가로 넘침 없음", ov["scroll"] <= ov["client"] and not ov["wide"], ov)
                b.shot("e2e-02-long-extracted-360"); b.viewport(1280)
        fresh(b)
        upload(b, P["한글 이름 파일.pdf"])
        check("한글 이름 파일.pdf: 파일 이름이 그대로 보임", "한글 이름 파일.pdf" in text_of(b, "doc-file"), text_of(b, "doc-file"))

        err_cases = [("scan.pdf", "NO_TEXT", "PDF를 읽지 못했어요"), ("tiny.pdf", "NO_TEXT", "PDF를 읽지 못했어요"),
                     ("encrypted.pdf", "ENCRYPTED", "PDF를 읽지 못했어요"), ("broken.pdf", "BROKEN_PDF", "PDF를 읽지 못했어요"),
                     ("fake_header.pdf", "BROKEN_PDF", "PDF를 읽지 못했어요"), ("fake.pdf", "NOT_PDF", "PDF를 읽지 못했어요"),
                     ("pages301.pdf", "TOO_LONG", "PDF를 읽지 못했어요"),
                     ("zero.pdf", "EMPTY_FILE", "이 파일은 올릴 수 없어요"), ("big.pdf", "FILE_TOO_LARGE", "이 파일은 올릴 수 없어요"),
                     ("note.txt", "NOT_PDF", "이 파일은 올릴 수 없어요")]
        if "real_rpi_guide.pdf" in P:
            err_cases.append(("real_rpi_guide.pdf", "FILE_TOO_LARGE", "이 파일은 올릴 수 없어요"))
        for name, code, title in err_cases:
            fresh(b)
            before = app.count("POST /api/extract")
            upload(b, P[name])
            pre = title == "이 파일은 올릴 수 없어요"
            sent = app.count("POST /api/extract") - before
            t = text_of(b, "error")
            check(f"추출 오류 {name} → {code}: 문구·'{title}'·[다른 파일]만·업로드 영역 그대로"
                  + (" · 서버 요청 0회(미리 거름)" if pre else f" · 서버 {code}"),
                  not hidden(b, "error") and EXPECT[code] in t and code not in t and text_of(b, "error-title") == title
                  and hidden(b, "retry-button") and not hidden(b, "upload") and hidden(b, "doc")
                  and (sent == 0 if pre else (sent == 1 and f" {code} " in app.log().splitlines()[-1] + " ")), [t, sent])
            if name == "scan.pdf":
                b.shot("e2e-03-error-scan-1280")
                b.viewport(480); b.shot("e2e-03-error-scan-480"); b.viewport(1280)
        check("추출 시간(화면 기준, 파일 선택→문서 카드): long.pdf 2초 이내", times["long.pdf"] < 2.0,
              {k: round(v, 2) for k, v in times.items()})
        print("       추출 시간(화면):", {k: round(v, 2) for k, v in times.items()})

        # ── 3. 드래그 앤 드롭(CDP 실제 드래그 이벤트) ──
        fresh(b)
        before = app.count("POST /api/extract")
        drag_files(b, [str(P["ko.pdf"]), str(P["en.pdf"])])
        b.wait(V["error"], 3)
        check("드롭: PDF 두 개를 한꺼번에 → MULTIPLE_FILES, 서버 요청 0회",
              EXPECT["MULTIPLE_FILES"] in text_of(b, "error-text") and app.count("POST /api/extract") == before)
        drag_files(b, [str(P["note.txt"])])
        b.wait("document.getElementById('error-text').textContent.includes('PDF 파일만')", 3)
        check("드롭: 글 파일(.txt) → NOT_PDF, 서버 요청 0회", EXPECT["NOT_PDF"] in text_of(b, "error-text")
              and app.count("POST /api/extract") == before)
        drag_files(b, [str(P["ko.pdf"])])
        b.wait(V["doc"], 5)
        check("드롭: 오류 화면에서 ko.pdf를 끌어다 놓기 → 읽기 완료", not hidden(b, "doc") and hidden(b, "error")
              and text_of(b, "doc-title") == mk.KO_TITLE)
        try:
            fresh(b)
            drag_files(b, [str(P["ko.pdf"].parent)])
            b.wait(V["error"] + " || " + V["doc"], 3)
            check("드롭: 폴더 → NOT_PDF(서버 요청 없음)", EXPECT["NOT_PDF"] in text_of(b, "error-text"), text_of(b, "error-text"))
        except RuntimeError as exc:
            check("드롭: 폴더(CDP가 폴더 드롭을 지원하지 않으면 확인 못 함)", True, exc)

        # ── 4. 주 흐름: ko → 요약 → 결과 → 복사 → 다른 파일 → en ──
        fresh(b)
        up_script([])
        upload(b, P["ko.pdf"])
        b.js("""window.__copied = null; { const o = navigator.clipboard.writeText.bind(navigator.clipboard);
                navigator.clipboard.writeText = (t) => { window.__copied = t; return o(t); }; }""")
        dt = summarize(b)
        lg = up_log()
        check("요약(ko): 결과 화면 — 한 줄 요약·요약·핵심 요점 3개·모델/호출/시간",
              not hidden(b, "result") and text_of(b, "result-one") == "가짜 업스트림이 만든 시험용 한 줄 요약이다."
              and b.js("document.querySelectorAll('#result-points li').length") == 3
              and text_of(b, "result-meta").startswith("모델 baidu/ernie-5.5-flash-preview:free · 호출 1회 · "), text_of(b, "result"))
        check("요약(ko): 업스트림 호출 1회·1순위 무료 모델·가짜 키만", len(lg) == 1 and lg[0]["model"].endswith(":free")
              and lg[0]["kind"] == "summary" and all(e["auth_fake"] for e in lg), lg)
        check("결과: 포커스가 '한 줄 요약' 제목으로", b.js("document.activeElement.id") == "result-one-title")
        b.shot("e2e-04-result-1280")
        b.viewport(480); b.shot("e2e-04-result-480")
        b.viewport(360); ov = cf.overflow(b); b.shot("e2e-04-result-360")
        check("결과 360px: 가로 넘침 없음", ov["scroll"] <= ov["client"] and not ov["wide"], ov)
        b.viewport(1280)
        b.click("#copy-button")
        b.wait("document.getElementById('copy-status').textContent", 3)
        copied = b.js("window.__copied") or ""
        check("복사: '복사했어요' + §7.2 형식(제목·한 줄 요약·요약·핵심 요점)",
              text_of(b, "copy-status") == "복사했어요" and copied.startswith(mk.KO_TITLE + "\n한 줄 요약: 가짜 업스트림이")
              and "\n\n요약\n" in copied and "\n\n핵심 요점\n- 첫째 요점이다." in copied, repr(copied)[:300])
        n_ch = len(b.chooser_events())
        b.click("#result .actions [data-action=another]")
        time.sleep(0.4)
        ev = b.chooser_events()
        check("[다른 파일 고르기] → 처음 화면 + 파일 고르기 창이 바로 열림", not hidden(b, "upload") and hidden(b, "result")
              and hidden(b, "doc") and len(ev) == n_ch + 1)
        if len(ev) > n_ch:
            b.call("DOM.setFileInputFiles", files=[str(P["en.pdf"])], backendNodeId=ev[-1]["params"]["backendNodeId"])
            b.wait(V["doc"], 5)
        check("고르기 창에서 en.pdf → 새 문서 카드", text_of(b, "doc-title") == mk.EN_TITLE, text_of(b, "doc-title"))

        # ── 5. 긴 문서·중간 문서: 호출 수 ──
        for name, want_trunc in (("long.pdf", True), ("mid.pdf", False)):
            fresh(b)
            up_script([])
            upload(b, P[name])
            dt = summarize(b)
            lg = up_log()
            check(f"요약({name}): 6만 자까지 한 번에 → 업스트림 1회·최종 요약 프롬프트, 결과 '호출 1회'"
                  + (" · 결과에 '앞 N쪽까지만 요약했어요'" if want_trunc else ""),
                  not hidden(b, "result") and len(lg) == 1 and lg[0]["kind"] == "summary"
                  and "호출 1회" in text_of(b, "result-meta")
                  and (("까지만 요약했어요" in text_of(b, "result-truncated")) if want_trunc else hidden(b, "result-truncated")),
                  [lg, text_of(b, "result-meta"), text_of(b, "result-truncated")])

        # ── 6. 요약 오류(가짜 업스트림) ──
        def sum_case(label, script, want_code, want_calls, retry: bool, extra=None):
            fresh(b)
            up_script(script)
            upload(b, P["ko.pdf"])
            summarize(b, 30)
            lg = up_log()
            t = text_of(b, "error")
            ok = (not hidden(b, "error") and EXPECT.get(want_code, "@@") in t and want_code not in t
                  and len(lg) == want_calls and (not hidden(b, "retry-button")) == retry and not hidden(b, "doc")
                  and text_of(b, "error-title") == "요약하지 못했어요")
            if extra:
                ok = ok and extra(t)
            check(f"요약 오류 {label} → {want_code} 문구, 업스트림 {want_calls}회, 다시 시도 {'있음' if retry else '없음'}, 문서 카드 유지",
                  ok, [t, len(lg), [e['model'] for e in lg]])

        reset_ms = int((time.time() // 86400 + 1) * 86400 * 1000)   # 다음 00:00 UTC = 09:00 KST
        want = cf.expected_reset(1)
        EXPECT["DAILY_LIMIT"] = "무료 요약 횟수를 다 썼어요"
        sum_case("하루 한도 429(reset 헤더)", [{"status": 429, "daily": True, "reset": reset_ms}], "DAILY_LIMIT", 1, False,
                 lambda t: f"{want}부터 다시 요약할 수 있어요" in t and "1분" not in t)
        b.shot("e2e-05-daily-limit-1280")
        b.viewport(480); b.shot("e2e-05-daily-limit-480"); b.viewport(1280)
        sum_case("하루 한도 429(헤더 없음)", [{"status": 429, "daily": True}], "DAILY_LIMIT", 1, False,
                 lambda t: f"{want}부터" in t)
        sum_case("분당 429 두 모델 모두", [{"status": 429}, {"status": 429}], "RATE_LIMIT", 2, True)
        b.click("#retry-button")
        b.wait(V["result"], 10)
        check("RATE_LIMIT 뒤 [다시 시도] → 다시 올리지 않고 결과", not hidden(b, "result")
              and len(up_log()) == 3, len(up_log()))
        sum_case("모델 응답이 JSON 아님(두 번)", [{"content": "요약을 못 하겠습니다"}, {"content": "역시 못 합니다"}], "BAD_OUTPUT", 2, True)
        sum_case("502 두 모델 모두", [{"status": 502}, {"status": 502}], "UPSTREAM", 2, True)
        sum_case("두 모델 모두 응답 없음(가짜 예산 6초×2)", [{"ok": True, "delay": 7}, {"ok": True, "delay": 7}], "TIMEOUT", 2, True)
        fresh(b)
        up_script([{"status": 500}])
        upload(b, P["ko.pdf"])
        summarize(b)
        check("1순위 500 → 폴백 모델로 결과(호출 2회, 모델 nemotron 표시)", not hidden(b, "result")
              and "nvidia/nemotron-3.5-super-vl:free · 호출 2회" in text_of(b, "result-meta"), text_of(b, "result-meta"))

        # ── 7. XSS: 제목·요약 문자열은 글자로만 ──
        fresh(b)
        up_script([{"summary": {"one_line": '<img src=x onerror="window.__xss=2">한 줄이다.',
                                "overview": "첫 문장이다. <script>window.__xss=3</script>둘째 문장이다.",
                                "points": ["<b>굵게</b> 요점이다.", "둘째 요점이다.", "셋째 요점이다."]}}])
        upload(b, P["xss_title.pdf"])
        summarize(b)
        check("XSS: PDF 제목·모델 응답의 HTML이 태그로 해석되지 않고 글자로 보임",
              b.js("window.__xss") is None and b.js("document.querySelectorAll('main img, main script, main b').length") == 0
              and "<img" in text_of(b, "doc-title") and "<img" in text_of(b, "result-one") and "<b>굵게</b>" in text_of(b, "result-points"),
              [text_of(b, "doc-title"), text_of(b, "result-one")])

        # ── 8. 취소·새로고침·연타 ──
        fresh(b)
        up_script([{"ok": True, "delay": 5}])
        upload(b, P["ko.pdf"])
        b.click("#summarize-button")
        b.wait(V["progress"], 2)
        time.sleep(1.0)
        b.click("#cancel-button")
        check("요약 취소 → 문서 카드로 돌아오고 '요약을 멈췄어요' 안내, [요약하기]에 포커스",
              hidden(b, "progress") and not hidden(b, "doc-stopped") and b.js("document.activeElement.id") == "summarize-button")
        t0 = time.monotonic()
        b.click("#summarize-button")
        b.wait(f"!({V['progress']}) && ({V['result']} || {V['error']})", 10)
        dt = time.monotonic() - t0
        time.sleep(0.3)
        check("취소 직후 다시 [요약하기] → BUSY 없이 결과(서버가 연결 끊김을 알아채 잠금을 놓음)",
              not hidden(b, "result") and dt < 4, [round(dt, 2), text_of(b, "error-text")])
        check("취소한 요약: 서버 로그 CLIENT_GONE(요청자 떠남), 업스트림은 취소 전 1회 + 다시 1회",
              "CLIENT_GONE" in app.log() and "요청자가 연결을 끊음" in app.log() and len(up_log()) == 2, len(up_log()))

        fresh(b)
        up_script([{"ok": True, "delay": 5}])
        upload(b, P["ko.pdf"])
        b.click("#summarize-button")
        b.wait(V["progress"], 2)
        time.sleep(1.0)
        n_gone = app.count("CLIENT_GONE")
        b.goto(BASE, READY)          # 요약 중 새로고침
        t0 = time.monotonic()
        st, _, raw = http("POST", BASE + "api/summarize", json.dumps({"text": "새로고침 뒤 다른 요청의 본문이다.", "meta": {}}).encode(),
                          {"Content-Type": "application/json"}, timeout=15)
        check("요약 중 새로고침 → 첫 화면, 서버는 그만두고 다음 요약 요청이 바로 200",
              b.js(V["upload"]) and st == 200 and time.monotonic() - t0 < 3 and app.count("CLIENT_GONE") == n_gone + 1,
              [st, raw[:120], round(time.monotonic() - t0, 2)])

        fresh(b)
        up_script([{"ok": True, "delay": 1.5}])
        upload(b, P["ko.pdf"])
        n_sum = app.count("POST /api/summarize")
        xy = b.center("#summarize-button")
        for _ in range(5):
            for t in ("mousePressed", "mouseReleased"):
                b.call("Input.dispatchMouseEvent", type=t, x=xy[0], y=xy[1], button="left", clickCount=1)
        b.key("Enter", "Enter", 13, "\r")
        b.wait(V["result"], 8)
        time.sleep(0.5)
        check("[요약하기] 연타(마우스 5번 + Enter) → 서버 요약 요청 1번, 업스트림 1회",
              app.count("POST /api/summarize") - n_sum == 1 and len(up_log()) == 1, [app.count("POST /api/summarize") - n_sum, len(up_log())])

        # ── 9. 서버 꺼짐 ──
        fresh(b)
        up_script([])
        upload(b, P["ko.pdf"])
        app.stop()
        summarize(b)
        check("요약 중 서버 꺼짐 → NETWORK('./run.sh가 실행 중인지') + 다시 시도, 문서 카드 유지",
              "./run.sh" in text_of(b, "error-text") and not hidden(b, "retry-button") and not hidden(b, "doc"), text_of(b, "error-text"))
        app.start()
        b.click("#retry-button")
        b.wait(V["result"], 10)
        check("서버를 다시 켜고 [다시 시도] → 결과", not hidden(b, "result"))
        fresh(b)
        app.stop()
        upload(b, P["ko.pdf"])
        check("업로드 중 서버 꺼짐 → NETWORK + 다시 시도(같은 파일)", "./run.sh" in text_of(b, "error-text")
              and not hidden(b, "retry-button"), text_of(b, "error-text"))
        app.start()
        b.click("#retry-button")
        b.wait(V["doc"], 5)
        check("서버를 다시 켜고 [다시 시도] → 같은 파일로 읽기 완료", not hidden(b, "doc") and text_of(b, "doc-title") == mk.KO_TITLE)

        # ── 10. 키 없음 · PDF 도구 없음 ──
        app.stop()
        empty_home = Path(tempfile.mkdtemp(prefix="nohome-", dir=out))
        app.start(key=False, home=str(empty_home))
        fresh(b)
        b.wait("document.getElementById('health-notices').children.length > 0", 3)
        check("키 없음: 첫 화면에 '요약 설정 없음' 미리 안내(config.py 진단 명령)", "요약 설정 없음" in text_of(b, "health-notices")
              and "python3 -B config.py" in text_of(b, "health-notices"), text_of(b, "health-notices"))
        b.shot("e2e-06-nokey-1280")
        up_script([])
        upload(b, P["ko.pdf"])
        summarize(b)
        check("키 없음: 추출은 되고 요약은 NO_KEY 문구, 업스트림 0회", not hidden(b, "doc") and EXPECT["NO_KEY"] in text_of(b, "error-text")
              and len(up_log()) == 0, [text_of(b, "error-text"), len(up_log())])
        app.stop()
        nobin = Path(tempfile.mkdtemp(prefix="nopath-", dir=out))
        app.start(path_env=str(nobin))
        fresh(b)
        b.wait("document.getElementById('health-notices').children.length > 0", 3)
        check("pdftotext 없음(PATH에서 뺌): 서버는 뜨고 첫 화면에 설치 안내", "sudo apt install poppler-utils" in text_of(b, "health-notices"),
              text_of(b, "health-notices"))
        upload(b, P["ko.pdf"])
        check("pdftotext 없음: 올리면 TOOL_MISSING 문구", EXPECT["TOOL_MISSING"] in text_of(b, "error-text"), text_of(b, "error-text"))
        b.shot("e2e-07-notools-1280")
        app.stop()
        app.start()

        # ── 11. 키보드만으로(실제 서버) ──
        fresh(b)
        up_script([])
        n0 = len(b.chooser_events())
        first = b.tab()
        b.key("Enter", "Enter", 13, "\r")
        time.sleep(0.4)
        ev = b.chooser_events()
        ok = first == "pick-button" and len(ev) == n0 + 1
        if len(ev) > n0:
            b.call("DOM.setFileInputFiles", files=[str(P["multi.pdf"])], backendNodeId=ev[-1]["params"]["backendNodeId"])
            b.wait(V["doc"], 5)
        ok = ok and b.js("document.activeElement.id") == "summarize-button"
        b.key(" ", "Space", 32, " ")
        b.wait(V["result"], 8)
        ok = ok and b.js("document.activeElement.id") == "result-one-title" and b.tab() == "copy-button"
        check("키보드만: Tab→Enter로 고르기 창 → 파일 → [요약하기]에 포커스 → Space → 결과(포커스 '한 줄 요약') → Tab [결과 복사]", ok,
              [first, len(ev) - n0])

        # ── 12. 보안 ──
        bodies = response_bodies(b)
        check("화면 소스·네트워크 응답 어디에도 'sk-or-v1-'·가짜 키 없음", not any("sk-or-v1-" in x or FAKE_KEY in x for x in bodies),
              len(bodies))
        log = app.log()
        leaks = [w for w in (mk.KO_PARAS[1][:25], mk.EN_PARAS[0][:25], "한글 이름 파일", mk.KO_TITLE, "sk-or-v1-", "Traceback",
                             "가짜 업스트림이 만든") if w in log]
        check("서버 로그에 본문·제목·파일 이름·요약·키·트레이스백 없음", not leaks, leaks)
        needle = mk.KO_PARAS[2][:30].encode()
        hits = files_with(APP_DIR, needle, started, out) + files_with(Path("/tmp"), needle, started, out)
        hits = [h for h in hits if "chrome-profile" not in h and "/pdfs/" not in h]
        check("시험 뒤 앱 폴더·/tmp에 업로드 문서 내용이 담긴 새 파일 없음", not hits, hits[:5])
        for path, want in [("/config.py", 404), ("/.env", 404), ("/server.py", 404), ("/tools/fake_upstream.py", 404),
                           ("/..%2fconfig.py", 404), ("//config.py", 404), ("/%2e%2e/config.py", 404), ("/APP.JS", 404), ("/index.html?x=1", 200), ("/app.js", 200)]:
            st, h, _ = http("GET", BASE.rstrip("/") + path)
            check(f"정적 허용 목록: GET {path} → {want}", st == want, st)
        st, _, _ = http("GET", BASE + "api/health", headers={"Host": "evil.example:8030"})
        check("Host가 다르면 403(DNS 리바인딩 방지)", st == 403, st)
        st, h, _ = http("POST", BASE + "api/summarize", b'{"text":"x"}', {"Content-Type": "application/json", "Origin": "http://evil.example"})
        check("다른 사이트 Origin의 요약 요청 → 403, CORS 헤더 없음", st == 403 and "Access-Control-Allow-Origin" not in h, st)
        st, h, _ = http("GET", BASE)
        check("정적 응답에 CSP·nosniff·X-Frame-Options", "default-src 'self'" in (h.get("Content-Security-Policy") or "")
              and h.get("X-Content-Type-Options") == "nosniff" and h.get("X-Frame-Options") == "DENY")

        # ── 13. 화면: 진행 중 · 콘솔 ──
        fresh(b)
        up_script([{"ok": True, "delay": 3}])
        upload(b, P["real_xz_ko.pdf"] if "real_xz_ko.pdf" in P else P["mid.pdf"])
        b.click("#summarize-button")
        b.wait(V["progress"], 2)
        check("긴 문서(2만 자 넘음) 요약 중: '긴 문서라 30초~1분쯤' 안내, 경과 초",
              "30초~1분" in text_of(b, "progress-hint") and text_of(b, "progress-title").startswith("요약하고 있어요…"))
        for w in (1280, 480, 360):
            b.viewport(w)
            b.shot(f"e2e-08-summarizing-{w}", full=False)
        b.viewport(1280)
        b.wait(V["result"], 8)
        errs = [e for e in b.errors() if "Failed to load resource" not in e]
        check("콘솔 오류·경고 0건(의도한 4xx/5xx 네트워크 기록 제외)", not errs, errs[:5])

        # ── 14. file:// ──
        b.goto((APP_DIR / "index.html").as_uri())
        time.sleep(1.6)
        check("file://로 열면 './run.sh' 실행 안내만 보임", b.js("getComputedStyle(document.getElementById('boot-notice')).visibility") == "visible"
              and "./run.sh" in text_of(b, "boot-notice") and b.js("getComputedStyle(document.getElementById('main')).display") == "none")
        b.shot("e2e-09-file-1280", full=False)
    finally:
        if b:
            b.close()
        app.stop()
        stop(upstream)
        shutil.rmtree(out / "chrome-profile", ignore_errors=True)
        wait_port(UP_PORT, False, 5)

    run_runsh_checks(out)


def run_runsh_checks(out: Path) -> None:
    """run.sh: 실행·이미 떠 있음·다른 프로그램 점유·Ctrl+C 뒷정리·다이어리(8020)와 동시 실행. 모델 호출 없음."""
    if port_busy(PORT):
        check("run.sh 시험: 8030이 비어 있어야 함", False)
        return
    t = Term(APP_DIR, runsh_env())
    try:
        up = t.wait_text("실행 중", 10) and wait_port(PORT, True, 5)
        st, h, raw = http("GET", BASE + "api/health")
        check("run.sh: 8030이 비었으면 서버 실행, /api/health 200·Server pdf-summary", up and st == 200
              and (h.get("Server") or "").startswith("pdf-summary"), t.read()[-300:])
        st2, _, page = http("GET", BASE)
        check("run.sh 서버의 첫 화면(index.html) 200", st2 == 200 and "한눈 PDF 요약" in page.decode())
        r = subprocess.run(["bash", "./run.sh"], cwd=APP_DIR, env=runsh_env(), capture_output=True, text=True, timeout=15)
        check("run.sh: 이미 떠 있으면 '이미 … 실행 중' 안내 후 종료코드 0(브라우저만)", r.returncode == 0 and "이미" in r.stdout,
              (r.returncode, r.stdout, r.stderr))
        diary = APP_DIR.parent / "run.sh"
        if diary.is_file() and not port_busy(8020):
            d = Term(APP_DIR.parent, runsh_env())
            try:
                both = wait_port(8020, True, 10) and http("GET", BASE + "api/health")[0] == 200
                check("다이어리(8020)와 동시 실행: 두 서버 모두 응답", both, d.read()[-200:])
            finally:
                d.ctrl_c()
                d.wait(8)
                d.kill()
                check("다이어리 Ctrl+C → 8020 닫힘", wait_port(8020, False, 5))
        else:
            check("다이어리 동시 실행(8020이 쓰이고 있거나 다이어리 run.sh가 없어 확인 못 함)", True)
        t.ctrl_c()
        code = t.wait(10)
        time.sleep(0.5)
        left = [p for p in pids_matching("server.py") if Path(f"/proc/{p}/cwd").exists()
                and os.path.realpath(f"/proc/{p}/cwd") == str(APP_DIR)]
        check("run.sh: Ctrl+C → run.sh 종료, server.py 남지 않음, 8030 닫힘", code is not None and not left and not port_busy(PORT),
              (code, left, t.read()[-200:]))
    finally:
        t.kill()
    # 다른 프로그램이 8030을 쓰고 있을 때
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", PORT))
        s.listen(1)
        r = subprocess.run(["bash", "./run.sh"], cwd=APP_DIR, env=runsh_env(), capture_output=True, text=True, timeout=15)
    check("run.sh: 다른 프로그램이 8030을 쓰면 한국어 안내 + 종료코드 1", r.returncode == 1 and "다른 프로그램" in r.stderr,
          (r.returncode, r.stderr))


# ── --live: run.sh + 실제 모델 ──────────────────────────────

def run_live(out: Path, P: dict[str, Path], docs: list[str], max_calls: int) -> None:
    sys.path.insert(0, str(APP_DIR))
    import config  # noqa: E402 — 진짜 키를 '비교'만 한다(찍지 않음)
    real_key = config.find_api_key()[0]
    if port_busy(PORT):
        check("--live: 8030이 비어 있어야 함", False)
        return
    t = Term(APP_DIR, runsh_env())
    b = None
    total_calls = 0
    try:
        check("--live: run.sh로 서버 실행", t.wait_text("실행 중", 10) and wait_port(PORT, True, 5), t.read()[-300:])
        cf.OUT = out
        b = cf.Browser()
        b.call("Emulation.setTimezoneOverride", timezoneId="Asia/Seoul")
        b.viewport(1280)
        for name in docs:
            if total_calls >= max_calls:
                print(f"       [멈춤] 이번 실행 호출 상한 {max_calls}회 — {name}부터 건너뜀")
                break
            fresh(b)
            upload(b, P[name])
            if hidden(b, "doc"):
                check(f"--live {name}: 추출", False, text_of(b, "error-text"))
                continue
            dt = summarize(b, 200)
            meta = text_of(b, "result-meta")
            calls = 0
            if "호출 " in meta:
                calls = int(meta.split("호출 ")[1].split("회")[0].replace(",", ""))
            elif not hidden(b, "error"):
                calls = -1   # 실패 — 서버 로그에서 센다
            if calls == -1:
                last = [ln for ln in t.read().splitlines() if "POST /api/summarize" in ln][-1:]
                m = re.search(r"호출 (\d+)회", last[0]) if last else None
                calls = int(m.group(1)) if m else 0
            total_calls += calls
            one = text_of(b, "result-one")
            korean = bool(re.search(r"[가-힣]", one))
            check(f"--live {name}: 실제 요약 결과(한국어) · {dt:.1f}s · 호출 {calls}회", not hidden(b, "result") and korean,
                  [text_of(b, "error-text"), meta])
            print(f"       한 줄 요약: {one}\n       {meta}")
            if not hidden(b, "result"):
                rec = {"file": name, "elapsed_s": round(dt, 1), "calls": calls, "meta": meta, "one_line": one,
                       "overview": text_of(b, "result-overview"),
                       "points": b.js("[...document.querySelectorAll('#result-points li')].map(li => li.textContent)"),
                       "truncated_note": text_of(b, "result-truncated")}
                print(f"       요약: {rec['overview']}")
                for pt in rec["points"]:
                    print(f"         - {pt}")
                with open(out / "live_results.jsonl", "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            b.shot(f"live-{Path(name).stem}-1280")
            if "무료 요약 횟수를 다 썼어요" in text_of(b, "error-text"):
                print("       [멈춤] 하루 한도 소진")
                break
        bodies = response_bodies(b)
        check("--live: 화면 소스·네트워크 응답에 진짜 키·'sk-or-v1-' 없음",
              not any(real_key in x or "sk-or-v1-" in x for x in bodies), len(bodies))
        log = t.read()
        check("--live: run.sh 출력(서버 로그)에 진짜 키·본문·제목 없음",
              real_key not in log and "sk-or-v1-" not in log.replace("sk-or-v1-…", "") and mk.KO_TITLE not in log
              and mk.KO_PARAS[1][:25] not in log)
    finally:
        if b:
            b.close()
        shutil.rmtree(out / "chrome-profile", ignore_errors=True)
        t.ctrl_c()
        code = t.wait(10)
        check("--live: Ctrl+C → run.sh 종료, 8030 닫힘", code is not None and wait_port(PORT, False, 5), code)
        (out / "live_runsh.log").write_text(t.read(0.3))
        t.kill()
    print(f"\n실제 모델 호출 합계(이번 실행): {total_calls}회 (모두 :free)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="스크린샷·로그·시험 PDF를 둘 저장소 밖 폴더")
    ap.add_argument("--pdfs", help="시험 PDF 폴더(없으면 OUT/pdfs에 만든다)")
    ap.add_argument("--live", action="store_true", help="run.sh + 실제 모델 호출")
    ap.add_argument("--docs", default="ko.pdf", help="--live에서 요약할 문서(쉼표)")
    ap.add_argument("--max-calls", type=int, default=6, help="--live 이번 실행의 실제 호출 상한(넘으면 남은 문서를 건너뜀)")
    a = ap.parse_args()
    out = Path(a.out).expanduser().resolve()
    if mk._inside_repo(out):
        print("--out은 저장소 밖이어야 합니다.")
        return 2
    out.mkdir(parents=True, exist_ok=True)
    for p in ((PORT,) if a.live else (PORT, UP_PORT)):
        if port_busy(p):
            print(f"포트 {p}이 이미 쓰이고 있습니다. 끄고 다시 실행하세요.")
            return 2
    P = ensure_pdfs(Path(a.pdfs).expanduser() if a.pdfs else out / "pdfs")
    t0 = time.monotonic()
    if a.live:
        run_live(out, P, [d.strip() for d in a.docs.split(",") if d.strip()], a.max_calls)
    else:
        run_fake(out, P)
    passed = sum(ok for _, ok, _ in results)
    print("\n" + "=" * 60)
    print(f"통합 점검 {passed}/{len(results)} 통과 · {time.monotonic() - t0:.0f}s · 스크린샷·로그: {out}"
          + ("" if a.live else " · 실제 모델 호출 0회"))
    for name, ok, _ in results:
        if not ok:
            print("  실패:", name)
    left = [p for p in (PORT, UP_PORT, 8031, 8032) if port_busy(p)]
    print(f"포트 8030·8031·8032·8033: {'열려 있음 ' + str(left) if left else '모두 닫힘'}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
