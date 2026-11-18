"""server.py의 HTTP 계약·오류 매핑 점검 (SPEC §9.2 backend). **모델 호출 0회.**

    python3 -B tools/check_server.py --pdfs DIR          # DIR에 시험 PDF가 없으면 make_test_pdfs로 만든다
    python3 -B tools/check_server.py --pdfs DIR --port 8030

- 실제 server.py를 별도 프로세스로 띄운다. 그 프로세스 안에서 `llm.summarize`를 가짜로 바꿔 끼우고,
  만일을 위해 `llm.API_URL`도 닿지 않는 로컬 주소로 바꾼다 → OpenRouter로 나가는 요청이 없다.
  llm.py가 없으면 가짜 llm 모듈을 만들어 넣는다.
- 기본 포트는 8032(실제 앱 8030·가짜 서버 8031과 겹치지 않게). 그 프로세스의 PORT·허용 Host/Origin을 같은 번호로 바꾼다.
- 서버 인스턴스 5개를 차례로 띄운다: 기본 / PDF 도구 없음·키 없음·llm·pdf_layout 없음 / pdftotext만 없음 /
  추출 제한시간 아주 짧게 / pdftohtml만 없음(3단계).
- (3단계, SPEC §10.11) /api/layout·/api/export: 형식·범위·오류 코드·Content-Disposition·HTML 안전성·처리 자리(BUSY).
  처리 자리 시험용으로 서버 프로세스 안의 pdf_layout.layout을 감싸, 본문에 SLOWLAYOUT이 든 PDF만 오래 걸리게 한다.
- 서버 로그는 DIR/check_server_logs/ 에 남긴다(저장소 밖). 끝나면 띄운 서버를 모두 끈다.
- 종료코드: 모두 통과 0, 실패 있으면 1.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))
import make_test_pdfs as mtp  # noqa: E402  (같은 tools/ 폴더. 앱 모듈이 아니다)
import check_layout as cl  # noqa: E402  (§10.4 형식·HTML 안전성 검사 함수를 함께 쓴다)

# 서버 프로세스 안에서 돌릴 부트스트랩. 앱 폴더를 sys.path 맨 앞에 넣는다(상위 std05의 같은 이름 모듈 금지).
BOOT = r'''
import json, sys, time, types
app, port, mode, extract_timeout = sys.argv[1], int(sys.argv[2]), sys.argv[3], float(sys.argv[4])
sys.path.insert(0, app)
import server, pdf_extract
server.PORT = port
if server.pdf_layout is not None and mode != "nollm":
    _real_layout = server.pdf_layout.layout
    def _slow_layout(data, *a, **k):
        if b"SLOWLAYOUT" in data:
            time.sleep(13)
        return _real_layout(data, *a, **k)
    server.pdf_layout.layout = _slow_layout
server.ALLOWED_HOSTS = {f"127.0.0.1:{port}", f"localhost:{port}"}
server.ALLOWED_ORIGINS = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
if extract_timeout > 0:
    pdf_extract.EXTRACT_TIMEOUT_S = extract_timeout

if mode == "nollm":
    server.llm = None
    server._LLM_IMPORT_ERROR = "가짜: llm 없음"
    server.pdf_layout = None
    server._LAYOUT_IMPORT_ERROR = "가짜: pdf_layout 없음"
else:
    if server.llm is None:
        class SummarizeError(Exception):
            def __init__(self, code, log="", reset_at=None):
                super().__init__(code); self.code = code; self.log = log; self.reset_at = reset_at
        server.llm = types.ModuleType("llm")
        server.llm.SummarizeError = SummarizeError
    llm = server.llm
    llm.API_URL = "http://127.0.0.1:9/never"   # 혹시 가짜가 빠져도 밖으로 나가지 않게
    def summarize(text, meta, should_stop=None):
        if text.startswith("@@ERR:"):
            parts = text.split(":")
            reset = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
            raise llm.SummarizeError(parts[1], log="가짜 실패", reset_at=reset)
        if text.startswith("@@SLEEP:"):
            end = time.monotonic() + float(text.split(":")[1])
            while time.monotonic() < end:          # 진짜 llm처럼 기다리는 동안 요청자가 떠났는지 본다
                if should_stop is not None and should_stop():
                    raise llm.SummarizeError("CANCELLED", log="가짜: 요청자 떠남")
                time.sleep(0.1)
        if text.startswith("@@CRASH"):
            raise RuntimeError("비밀 본문이 섞인 예외 메시지")
        if text.startswith("@@BADRET"):
            return {"one_line": "x"}
        return {"one_line": f"len={len(text)}", "overview": json.dumps(meta, ensure_ascii=False, sort_keys=True),
                "points": ["가", "나", "다"], "model": "fake/model:free", "calls": 1, "chunks": 1}
    llm.summarize = summarize
server.main()
'''

# 이번 실행에만 쓰는 표식. 업로드 본문·요약 입력에 넣고, 시험 뒤 새로 생긴 파일·로그에 이 표식이 있는지 본다.
# (같은 기기에서 다른 에이전트가 시험 문서 문장을 출력하는 파일과 섞이지 않게 고유 문자열을 쓴다)
NONCE = "NONCE" + os.urandom(8).hex().upper()


def nonce_pdf(text: str) -> bytes:
    """글자(ASCII)가 든 한 쪽짜리 최소 PDF. pdftotext로 text가 그대로 나온다."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


SECRET_SENTENCES = [mtp.KO_PARAS[1][:30], mtp.INJECT_PARAS[1][:20], mtp.EN_PARAS[0][:40], mtp.LONG_PARA[:30]]


class Checker:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[str] = []

    def ok(self, cond: bool, label: str, detail: str = "") -> bool:
        if cond:
            self.passed += 1
            print(f"  ok   {label}")
        else:
            self.failed.append(label)
            print(f"  FAIL {label}  {detail}")
        return cond


class Resp:
    def __init__(self, status: int, headers: dict, body: bytes):
        self.status = status
        self.headers = headers
        self.body = body

    def json(self) -> dict:
        try:
            return json.loads(self.body.decode("utf-8"))
        except ValueError:
            return {}

    @property
    def code(self) -> str | None:
        err = self.json().get("error")
        return err.get("code") if isinstance(err, dict) else None


def req(port: int, method: str, path: str, body: bytes | None = None, headers: dict | None = None,
        timeout: float = 30) -> Resp:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        hdrs = {"Host": f"127.0.0.1:{port}"}
        hdrs.update(headers or {})
        if body is not None and "Content-Length" not in hdrs:
            hdrs["Content-Length"] = str(len(body))
        for k, v in hdrs.items():
            if v is not None:
                conn.putheader(k, v)
        conn.endheaders()
        if body:
            try:
                conn.send(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # 서버가 본문을 다 받기 전에 응답하고 닫은 경우 — 응답은 아래에서 읽는다
        r = conn.getresponse()
        return Resp(r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
    finally:
        conn.close()


def raw_send(port: int, data: bytes, *, read: bool = True, timeout: float = 5, half_close: bool = False) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
        s.sendall(data)
        if half_close:
            s.shutdown(socket.SHUT_WR)   # "더 보낼 것 없음" — 본문이 잘린 채 끝난 요청
        if not read:
            return b""
        out = b""
        try:
            while chunk := s.recv(65536):
                out += chunk
        except OSError:
            pass
        return out


def port_free(port: int) -> bool:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


class ServerProc:
    def __init__(self, port: int, log_path: Path, *, mode: str = "fake", extract_timeout: float = 0,
                 env: dict | None = None, cwd: Path):
        self.port = port
        self.log_path = log_path
        self.log_file = open(log_path, "wb")
        self.proc = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", BOOT, str(APP_DIR), str(port), mode, str(extract_timeout)],
            stdout=self.log_file, stderr=subprocess.STDOUT, env=env, cwd=cwd,
        )
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"서버가 바로 끝났습니다. 로그: {log_path}")
            try:
                if req(port, "GET", "/api/health", timeout=1).status == 200:
                    return
            except OSError:
                pass
            time.sleep(0.1)
        self.stop()
        raise RuntimeError(f"서버가 응답하지 않습니다. 로그: {log_path}")

    def stop(self) -> str:
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.log_file.close()
        return self.log_path.read_text(encoding="utf-8", errors="replace")


def snapshot(dirs: list[Path], skip: Path) -> dict[str, float]:
    seen: dict[str, float] = {}
    for d in dirs:
        for root, subdirs, files in os.walk(d, onerror=lambda e: None):
            if Path(root).resolve().is_relative_to(skip):
                subdirs[:] = []
                continue
            for f in files:
                p = os.path.join(root, f)
                try:
                    seen[p] = os.stat(p).st_mtime
                except OSError:
                    pass
    return seen


def pdf_headers(name: str | None) -> dict:
    h = {"Content-Type": "application/pdf"}
    if name is not None:
        h["X-Filename"] = urllib.parse.quote(name)
    return h


def json_body(obj: object) -> tuple[bytes, dict]:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8"), {"Content-Type": "application/json"}


# ── 점검 묶음 ─────────────────────────────────────────────────────────

def check_extract_cases(c: Checker, port: int, pdfs: Path, timings: dict) -> None:
    print("\n[추출] SPEC §9.1 시험 PDF")
    for name, case in mtp.CASES.items():
        path = pdfs / name
        if not path.is_file():
            c.ok(False, f"{name} 파일 있음", str(path))
            continue
        data = path.read_bytes()
        t0 = time.monotonic()
        r = req(port, "POST", "/api/extract", data, pdf_headers(name), timeout=60)
        timings[name] = time.monotonic() - t0
        label = f"{name} → {case['status']} {case['code'] or ''}".rstrip()
        got = f"got {r.status} {r.code}"
        if not c.ok(r.status == case["status"] and r.code == case["code"], label, got):
            continue
        if r.status != 200:
            continue
        body = r.json()
        meta = body.get("meta", {})
        text = body.get("text", "")
        want = dict(case.get("meta", {}))
        want.setdefault("file_name", name)
        want["file_bytes"] = len(data)
        bad = {k: (meta.get(k), v) for k, v in want.items() if meta.get(k) != v}
        c.ok(not bad, f"{name} meta 값", f"(받음, 기대) {bad}")
        keys = {"file_name", "file_bytes", "title", "pages", "pages_used", "empty_pages", "chars",
                "chars_total", "truncated"}
        c.ok(set(meta) == keys, f"{name} meta 키 정확히 §4.2", str(sorted(set(meta) ^ keys)))
        c.ok(meta.get("chars") == len(text), f"{name} chars == len(text)")
        if "contains" in case:
            c.ok(case["contains"] in text, f"{name} 본문에 '{case['contains']}'")
        if name == "long.pdf":
            c.ok(meta["chars"] <= 60000 and meta["chars_total"] > 60000 and meta["pages_used"] < meta["pages"],
                 "long.pdf chars≤60000·chars_total>60000·pages_used<pages", str(meta))
        if name == "mid.pdf":
            c.ok(20000 < meta["chars"] <= 40000, "mid.pdf 2만<chars≤4만(2조각)", str(meta["chars"]))
        if not meta.get("truncated"):
            c.ok(meta.get("pages_used") == meta.get("pages") and meta.get("chars") == meta.get("chars_total"),
                 f"{name} 안 잘림 → pages_used==pages, chars==chars_total")
    if "long.pdf" in timings:
        c.ok(timings["long.pdf"] < 2.0, f"long.pdf 추출 2초 이내 (실측 {timings['long.pdf']:.2f}s)")

    # 큰 파일: Expect 없이 본문을 바로 보내는 브라우저 방식에서도 413 응답을 받아야 한다
    big = (pdfs / "big.pdf").read_bytes()
    r = req(port, "POST", "/api/extract", big, pdf_headers("big.pdf"))
    c.ok(r.status == 413 and r.code == "FILE_TOO_LARGE" and r.headers.get("connection") == "close",
         "big.pdf(본문 바로 전송) → 413 FILE_TOO_LARGE + Connection: close", f"{r.status} {r.code}")
    # curl처럼 Expect: 100-continue를 보내면 100 없이 바로 413
    head = (f"POST /api/extract HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/pdf\r\n"
            f"Content-Length: {len(big)}\r\nExpect: 100-continue\r\n\r\n").encode()
    out = raw_send(port, head)
    c.ok(out.startswith(b"HTTP/1.1 413") and b"100 Continue" not in out,
         "Expect: 100-continue + 큰 본문 → 100 없이 413", out[:40].decode(errors="replace"))
    # 파일 이름 정리
    r = req(port, "POST", "/api/extract", (pdfs / "ko.pdf").read_bytes(),
            {"Content-Type": "application/pdf", "X-Filename": urllib.parse.quote("../../etc/보고서\x01.pdf")})
    c.ok(r.json().get("meta", {}).get("file_name") == "보고서.pdf", "X-Filename 경로·제어문자 제거",
         str(r.json().get("meta", {}).get("file_name")))
    r = req(port, "POST", "/api/extract", (pdfs / "ko.pdf").read_bytes(),
            {"Content-Type": "application/pdf", "X-Filename": "%FF%FE"})
    c.ok(r.status == 200 and r.json()["meta"]["file_name"] == "", "X-Filename 디코딩 실패 → 빈 문자열")
    r = req(port, "POST", "/api/extract", (pdfs / "ko.pdf").read_bytes(), {"Content-Type": "application/pdf"})
    c.ok(r.status == 200 and r.json()["meta"]["file_name"] == "", "X-Filename 없음 → 빈 문자열")
    r = req(port, "POST", "/api/extract", (pdfs / "ko.pdf").read_bytes(),
            {"Content-Type": "application/pdf", "X-Filename": urllib.parse.quote("가" * 300 + ".pdf")})
    c.ok(len(r.json()["meta"]["file_name"]) == 200, "X-Filename 200자로 자름")
    # 표식 문서: 디스크·로그 점검용(이 문자열이 시험 뒤 어디에도 남지 않아야 한다)
    marker = f"Secret marker {NONCE} paragraph for the disk check"
    r = req(port, "POST", "/api/extract", nonce_pdf(marker), pdf_headers(f"{NONCE}.pdf"))
    c.ok(r.status == 200 and NONCE in r.json().get("text", ""), "표식 PDF 추출(직접 만든 최소 PDF)", f"{r.status} {r.code}")
    r = req(port, "POST", "/api/extract", b"%PDF-1.4\n" + marker.encode() * 50, pdf_headers(f"{NONCE}-b.pdf"))
    c.ok(r.status == 422 and r.code == "BROKEN_PDF", "표식 깨진 PDF → BROKEN_PDF")
    body, h = json_body({"text": marker, "meta": {"title": NONCE, "file_name": f"{NONCE}.pdf"}})
    r = req(port, "POST", "/api/summarize", body, h)
    c.ok(r.status == 200, "표식 요약 입력 → 200")


def check_request_format(c: Checker, port: int, pdfs: Path) -> None:
    print("\n[요청 형식] BAD_REQUEST")
    ko = (pdfs / "ko.pdf").read_bytes()
    r = req(port, "POST", "/api/extract", ko, {"Content-Type": None})
    c.ok(r.status == 415 and r.code == "BAD_REQUEST", "Content-Type 없음 → 415 BAD_REQUEST", f"{r.status} {r.code}")
    r = req(port, "POST", "/api/extract", ko, {"Content-Type": "application/json"})
    c.ok(r.status == 415 and r.code == "BAD_REQUEST", "application/json으로 PDF → 415 BAD_REQUEST", f"{r.status} {r.code}")
    r = req(port, "POST", "/api/extract", ko, {"Content-Type": "application/pdf; charset=binary"})
    c.ok(r.status == 200, "Content-Type 매개변수(;…)는 허용", f"{r.status}")
    out = raw_send(port, (f"POST /api/extract HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                          f"Content-Type: application/pdf\r\nConnection: close\r\n\r\n").encode() + ko[:100])
    c.ok(out.startswith(b"HTTP/1.1 411") and b'"BAD_REQUEST"' in out, "Content-Length 없음 → 411 BAD_REQUEST",
         out[:30].decode(errors="replace"))
    r = req(port, "POST", "/api/extract", ko, {"Content-Type": "application/pdf", "Content-Length": "abc"})
    c.ok(r.status == 411 and r.code == "BAD_REQUEST", "Content-Length 숫자 아님 → 411", f"{r.status} {r.code}")
    out = raw_send(port, (f"POST /api/extract HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/pdf\r\n"
                          f"Content-Length: 1000\r\n\r\n").encode() + ko[:10], half_close=True)
    c.ok(out.startswith(b"HTTP/1.1 400") and b'"BAD_REQUEST"' in out, "본문이 Content-Length보다 짧음 → 400 BAD_REQUEST",
         out[:30].decode(errors="replace"))
    r = req(port, "GET", "/api/extract")
    c.ok(r.status == 405 and r.code == "METHOD_NOT_ALLOWED" and r.headers.get("allow") == "POST",
         "GET /api/extract → 405 + Allow: POST", f"{r.status} {r.headers.get('allow')}")
    r = req(port, "OPTIONS", "/api/summarize")
    c.ok(r.status == 405 and "access-control-allow-origin" not in r.headers, "OPTIONS → 405, CORS 헤더 없음")
    r = req(port, "POST", "/api/health", b"{}", {"Content-Type": "application/json"})
    c.ok(r.status == 405, "POST /api/health → 405", f"{r.status}")
    r = req(port, "POST", "/nope", b"{}", {"Content-Type": "application/json"})
    c.ok(r.status == 404 and r.code == "NOT_FOUND", "POST /nope → 404")
    r = req(port, "GET", "/api/nope")
    c.ok(r.status == 404 and r.code == "NOT_FOUND", "GET /api/nope → 404")
    out = raw_send(port, f"FOO / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
    c.ok(out.startswith(b"HTTP/1.1 405") and b"METHOD_NOT_ALLOWED" in out, "모르는 메서드 FOO → 405 JSON")
    out = raw_send(port, b"GARBAGE\r\n\r\n")
    c.ok(b"Traceback" not in out and (out == b"" or b"BAD_REQUEST" in out), "깨진 요청 줄 → JSON 400 또는 끊김")


def check_host_origin_static(c: Checker, port: int) -> None:
    print("\n[Host·Origin·정적 파일]")
    r = req(port, "GET", "/api/health", headers={"Host": f"evil.com:{port}"})
    c.ok(r.status == 403 and r.code == "FORBIDDEN_HOST", "Host evil.com → 403")
    r = req(port, "GET", "/", headers={"Host": f"evil.com:{port}"})
    c.ok(r.status == 403, "정적 파일도 Host 검사 → 403")
    r = req(port, "GET", "/api/health", headers={"Host": f"localhost:{port}"})
    c.ok(r.status == 200, "Host localhost → 200")
    body, h = json_body({"text": "본문"})
    for origin in ("http://evil.com", "null", f"http://127.0.0.1:{port}.evil.com"):
        r = req(port, "POST", "/api/summarize", body, {**h, "Origin": origin})
        c.ok(r.status == 403 and r.code == "FORBIDDEN_HOST", f"POST Origin: {origin} → 403", f"{r.status}")
    r = req(port, "POST", "/api/extract", b"%PDF-1.4", {**pdf_headers(None), "Origin": "null"})
    c.ok(r.status == 403, "POST /api/extract Origin: null → 403")
    r = req(port, "POST", "/api/summarize", body, {**h, "Origin": f"http://localhost:{port}"})
    c.ok(r.status == 200, "POST Origin: http://localhost:PORT → 200", f"{r.status}")
    r = req(port, "GET", "/api/health", headers={"Origin": "http://evil.com"})
    c.ok(r.status == 200 and "access-control-allow-origin" not in r.headers, "GET은 Origin 검사 안 함, CORS 헤더 없음")

    for path in ("/config.py", "/.env", "/server.py", "/tools/", "/..%2fconfig.py", "/%2e%2e/config.py",
                 "/tools/check_server.py", "/pdf_extract.py", "/llm.py", "/SPEC.md", "/.env.example",
                 "/run.sh", "/INDEX.HTML", "/index.html/", "/static/../config.py", "/pdf_layout.py",
                 "/tools/check_layout.py", "/VIEWER.HTML", "/viewer.html/", "/viewer"):
        r = req(port, "GET", path)
        c.ok(r.status == 404 and r.code == "NOT_FOUND", f"GET {path} → 404", f"{r.status}")
    for path in ("/", "/index.html", "/index.html?x=1", "/style.css", "/app.js",
                 "/viewer.html", "/viewer.html?x=1#doc=abc", "/viewer.css", "/viewer.js"):
        name = {"/": "index.html"}.get(path, path.split("?")[0].split("#")[0].lstrip("/"))
        r = req(port, "GET", path)
        if not (APP_DIR / name).is_file():
            c.ok(r.status == 404, f"GET {path} → 404 (아직 {name} 없음)")
            continue
        c.ok(r.status == 200 and "content-security-policy" in r.headers and "frame-ancestors 'none'" in r.headers["content-security-policy"],
             f"GET {path} → 200 + CSP", f"{r.status}")
    r = req(port, "HEAD", "/api/health")
    c.ok(r.status == 200 and r.body == b"", "HEAD /api/health → 200, 본문 없음")


def check_health(c: Checker, port: int, *, tools: dict, key: bool, label: str,
                 layout_tools: dict | None = None) -> None:
    r = req(port, "GET", "/api/health")
    body = r.json()
    lt = layout_tools if layout_tools is not None else {"pdftohtml": True, "pdftoppm": True}
    c.ok(r.status == 200 and body == {"ok": True, "app": "pdf-summary", "tools": tools, "key": key, "layout_tools": lt},
         f"{label} /api/health = tools {tools}, key {key}, layout_tools {lt}", str(body))
    h = r.headers
    c.ok(h.get("server", "").startswith("pdf-summary") and h.get("content-type") == "application/json; charset=utf-8"
         and h.get("cache-control") == "no-store" and h.get("x-content-type-options") == "nosniff",
         f"{label} 공통 헤더(Server·Content-Type·no-store·nosniff)", str(h))


def check_summarize(c: Checker, port: int) -> None:
    print("\n[요약] 가짜 llm.summarize")
    body, h = json_body({"text": "  본문입니다  ", "meta": {
        "title": "제" * 300, "file_name": "a.pdf", "pages": "5", "pages_used": True, "truncated": "yes",
        "extra": "버려져야 함", "chars": 3}})
    r = req(port, "POST", "/api/summarize", body, h)
    j = r.json()
    c.ok(r.status == 200 and set(j) == {"one_line", "overview", "points", "model", "calls", "chunks", "elapsed_ms"},
         "정상 → 200, 응답 키 정확히 §4.2", str(j)[:200])
    c.ok(j.get("one_line") == "len=5", "text 앞뒤 공백 제거 후 llm에 전달", str(j.get("one_line")))
    try:
        meta_seen = json.loads(j.get("overview", "{}"))
    except ValueError:
        meta_seen = {}
    want = {"title": "제" * 200, "file_name": "a.pdf", "pages": 0, "pages_used": 0, "truncated": False}
    c.ok(meta_seen == want, "meta 정리(§5.2 키만·200자·틀린 타입은 기본값)", str(meta_seen)[:200])
    c.ok(isinstance(j.get("elapsed_ms"), int), "elapsed_ms는 서버가 잰 정수")
    body, h = json_body({"text": "본문"})
    r = req(port, "POST", "/api/summarize", body, h)
    c.ok(r.status == 200 and json.loads(r.json()["overview"]) ==
         {"title": "", "file_name": "", "pages": 0, "pages_used": 0, "truncated": False}, "meta 없음 → 기본값")

    expected = {"NO_KEY": 500, "RATE_LIMIT": 429, "DAILY_LIMIT": 429, "TIMEOUT": 504, "UPSTREAM": 502,
                "BAD_OUTPUT": 502, "TOO_LONG": 413}
    for code, status in expected.items():
        body, h = json_body({"text": f"@@ERR:{code}"})
        r = req(port, "POST", "/api/summarize", body, h)
        c.ok(r.status == status and r.code == code and "reset_at" not in r.json()["error"],
             f"SummarizeError {code} → {status}", f"{r.status} {r.code}")
    body, h = json_body({"text": "@@ERR:DAILY_LIMIT:1795395600000"})
    r = req(port, "POST", "/api/summarize", body, h)
    c.ok(r.status == 429 and r.json()["error"].get("reset_at") == 1795395600000, "DAILY_LIMIT reset_at 실림",
         str(r.json()))
    body, h = json_body({"text": "@@ERR:WHATEVER"})
    r = req(port, "POST", "/api/summarize", body, h)
    c.ok(r.status == 500 and r.code == "INTERNAL", "모르는 SummarizeError 코드 → 500 INTERNAL")
    body, h = json_body({"text": "@@CRASH"})
    r = req(port, "POST", "/api/summarize", body, h)
    c.ok(r.status == 500 and r.code == "INTERNAL" and "비밀" not in r.body.decode(), "예상 못 한 예외 → INTERNAL, 메시지 숨김")
    body, h = json_body({"text": "@@BADRET"})
    r = req(port, "POST", "/api/summarize", body, h)
    c.ok(r.status == 500 and r.code == "INTERNAL", "llm 반환 형식 위반 → INTERNAL")

    cases = [
        (b"{bad", "application/json", 400, "BAD_REQUEST", "JSON 해석 실패"),
        (b"[1]", "application/json", 400, "BAD_REQUEST", "JSON 객체 아님"),
        (b'{"text": 3}', "application/json", 400, "BAD_REQUEST", "text가 문자열 아님"),
        (b'{"text": "   \\n "}', "application/json", 400, "BAD_REQUEST", "text 공백뿐"),
        (b'{"text": "a\\ud800b"}', "application/json", 400, "BAD_REQUEST", "짝 없는 서로게이트"),
        (b"\xff\xfe", "application/json", 400, "BAD_REQUEST", "UTF-8 아님"),
        (b'{"text": "a"}', "text/plain", 415, "BAD_REQUEST", "Content-Type text/plain"),
        (json.dumps({"text": "가" * 60001}).encode(), "application/json", 413, "TOO_LONG", "60,001자"),
        (b'{"text": "' + b"a" * (512 * 1024) + b'"}', "application/json", 413, "BAD_REQUEST", "본문 > 512KiB"),
    ]
    for raw, ctype, status, code, label in cases:
        r = req(port, "POST", "/api/summarize", raw, {"Content-Type": ctype})
        c.ok(r.status == status and r.code == code, f"{label} → {status} {code}", f"{r.status} {r.code}")
    body, h = json_body({"text": "가" * 60000})
    r = req(port, "POST", "/api/summarize", body, h)
    c.ok(r.status == 200, "60,000자 정확히 → 통과", f"{r.status}")

    print("\n[요약] 동시 요청 BUSY")
    results: dict[str, Resp] = {}

    def slow():
        b, hh = json_body({"text": "@@SLEEP:5"})
        results["a"] = req(port, "POST", "/api/summarize", b, hh)
    t = threading.Thread(target=slow)
    t.start()
    time.sleep(0.7)
    body, h = json_body({"text": "두 번째"})
    t0 = time.monotonic()
    r = req(port, "POST", "/api/summarize", body, h)
    waited = time.monotonic() - t0
    c.ok(r.status == 409 and r.code == "BUSY" and 1.5 <= waited < 3.5,
         "요약 진행 중 두 번째 요청 → BUSY_WAIT_S(2초) 기다린 뒤 409 BUSY", f"{r.status} {r.code} {waited:.1f}s")
    r = req(port, "POST", "/api/extract", b"%PDF-1.4\n", pdf_headers(None))
    c.ok(r.status == 422, "요약 진행 중에도 추출은 됨(잠금 없음)", f"{r.status}")
    t.join()
    c.ok(results.get("a") is not None and results["a"].status == 200, "첫 요청은 200")
    r = req(port, "POST", "/api/summarize", body, h)
    c.ok(r.status == 200, "끝난 뒤 다시 → 200 (잠금 풀림)")
    body, h = json_body({"text": "@@ERR:UPSTREAM"})
    req(port, "POST", "/api/summarize", body, h)
    body, h = json_body({"text": "다시"})
    r = req(port, "POST", "/api/summarize", body, h)
    c.ok(r.status == 200, "실패 뒤에도 잠금 풀림")

    print("\n[요약] 취소(연결 끊김) 뒤 바로 다시")
    raw = json.dumps({"text": "@@SLEEP:20"}).encode()
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall((f"POST /api/summarize HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\n"
                   f"Content-Length: {len(raw)}\r\n\r\n").encode() + raw)
        time.sleep(0.5)
    t0 = time.monotonic()                    # 브라우저의 [요약 취소] = fetch 중단 = 연결 닫힘
    body, h = json_body({"text": "취소 뒤 다시"})
    r = req(port, "POST", "/api/summarize", body, h)
    c.ok(r.status == 200 and time.monotonic() - t0 < 2.0,
         "요약 중 연결을 끊으면 서버가 그만두고 잠금을 놓음 → 바로 다시 요청해도 200(BUSY 아님)",
         f"{r.status} {r.code} {time.monotonic() - t0:.1f}s")


def check_disconnects(c: Checker, port: int, pdfs: Path) -> None:
    print("\n[연결 끊김]")
    ko = (pdfs / "ko.pdf").read_bytes()
    head = (f"POST /api/extract HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/pdf\r\n"
            f"Content-Length: {len(ko)}\r\n\r\n").encode()
    raw_send(port, head + ko[:1000], read=False)          # 업로드 도중 끊기
    body = json.dumps({"text": "@@SLEEP:1.5"}).encode()
    raw_send(port, (f"POST /api/summarize HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n\r\n").encode() + body, read=False)  # 응답 기다리다 끊기
    time.sleep(2.0)
    r = req(port, "GET", "/api/health")
    c.ok(r.status == 200, "끊긴 뒤에도 서버 정상")


# ── 3단계: /api/layout · /api/export (SPEC §10.3·§10.11) ─────────────────

def check_layout_api(c: Checker, port: int, pdfs: Path, timings: dict) -> None:
    print("\n[보기] POST /api/layout")
    struct = (pdfs / "struct.pdf").read_bytes()
    t0 = time.monotonic()
    r = req(port, "POST", "/api/layout?from=1&to=5", struct, pdf_headers("struct.pdf"), timeout=90)
    timings["struct.pdf layout 1~5"] = time.monotonic() - t0
    j = r.json()
    c.ok(r.status == 200 and r.headers.get("content-type") == "application/json; charset=utf-8"
         and r.headers.get("cache-control") == "no-store", "struct.pdf → 200 JSON, no-store", f"{r.status} {r.code}")
    probs = cl.schema_problems(j) if r.status == 200 else ["응답 없음"]
    c.ok(not probs, "struct.pdf 응답이 §10.4 형식", str(probs[:3]))
    if r.status == 200:
        blocks = [b for p in j["pages"] for b in p["blocks"]]
        tables = [b for b in blocks if b["type"] == "table"]
        figs = [b for b in blocks if b["type"] == "figure"]
        c.ok(len(j["doc"]["outline"]) == 4 and len(tables) == 1 and len(figs) == 1 and figs[0]["src"]
             and [[x["text"] for x in row] for row in tables[0]["rows"]] == mtp.STRUCT_TABLE,
             "struct.pdf 책갈피 4·표 1(셀 일치)·그림 1")
    for name, case in mtp.LAYOUT_CASES.items():
        if name == "struct.pdf":
            continue
        r = req(port, "POST", "/api/layout", (pdfs / name).read_bytes(), pdf_headers(name), timeout=90)
        c.ok(r.status == 200 and not cl.schema_problems(r.json()), f"{name} → 200, 형식 통과", f"{r.status} {r.code}")
    for name in ("scan.pdf", "tiny.pdf", "mixed.pdf"):
        r = req(port, "POST", "/api/layout", (pdfs / name).read_bytes(), pdf_headers(name), timeout=90)
        c.ok(r.status == 200, f"{name} → 200 (NO_TEXT를 내지 않음)", f"{r.status} {r.code}")

    print("\n[보기] 쿼리 from·to·body")
    longd = (pdfs / "long.pdf").read_bytes()
    multi = (pdfs / "multi.pdf").read_bytes()
    for q, want in (("", (1, 10)), ("?from=3", (3, 12)), ("?from=5&to=40", (5, 14)), ("?from=70&to=999", (70, 78)),
                    ("?from=2&to=2&x=1&from=9", (2, 2)), ("?to=3", (1, 3))):
        r = req(port, "POST", "/api/layout" + q, longd, pdf_headers(None), timeout=90)
        got = (r.json().get("from"), r.json().get("to"))
        c.ok(r.status == 200 and got == want, f"/api/layout{q or ' (쿼리 없음)'} → {want[0]}~{want[1]}쪽",
             f"{r.status} {got}")
    for q in ("?from=abc", "?from=1.5", "?from=0", "?from=6", "?from=3&to=2", "?from=-1", "?from=", "?to=x",
              "?from=%EF%BC%91"):
        r = req(port, "POST", "/api/layout" + q, multi, pdf_headers(None))
        c.ok(r.status == 400 and r.code == "BAD_REQUEST", f"/api/layout{q} (5쪽 문서) → 400 BAD_REQUEST",
             f"{r.status} {r.code}")
    r = req(port, "POST", "/api/layout?from=1&to=2&body=9.5", multi, pdf_headers(None))
    c.ok(r.status == 200 and r.json().get("body_size") == 9.5, "body=9.5 → body_size 9.5")
    for q in ("body=abc", "body=100", "body=nan", "body=-3"):
        r = req(port, "POST", "/api/layout?" + q, multi, pdf_headers(None))
        c.ok(r.status == 200 and r.json().get("body_size") == 12.0, f"{q} → 무시하고 문서로 계산(12)",
             f"{r.status} {r.json().get('body_size')}")
    t0 = time.monotonic()
    r = req(port, "POST", "/api/layout?from=1&to=5", longd, pdf_headers(None), timeout=90)
    timings["long.pdf layout 1~5"] = time.monotonic() - t0
    t0 = time.monotonic()
    f = 1
    first = None
    while f <= 78:
        q = f"?from={f}&to={f + 4}" + (f"&body={first}" if first else "")
        r = req(port, "POST", "/api/layout" + q, longd, pdf_headers(None), timeout=90)
        first = first or r.json().get("body_size")
        f += 5
    timings["long.pdf layout 전체(5쪽씩 16번)"] = time.monotonic() - t0

    print("\n[보기·내려받기] 오류 코드가 /api/extract와 같다")
    for route in ("/api/layout", "/api/export"):
        for name, case in mtp.CASES.items():
            if case["status"] == 200 or case["code"] == "NO_TEXT":
                continue
            r = req(port, "POST", route, (pdfs / name).read_bytes(), pdf_headers(name), timeout=60)
            c.ok(r.status == case["status"] and r.code == case["code"],
                 f"{route} {name} → {case['status']} {case['code']}", f"{r.status} {r.code}")
        r = req(port, "POST", route, struct, {"Content-Type": "application/json"})
        c.ok(r.status == 415 and r.code == "BAD_REQUEST", f"{route} application/json → 415")
        r = req(port, "POST", route, struct, {"Content-Type": "application/pdf", "Content-Length": "x"})
        c.ok(r.status == 411 and r.code == "BAD_REQUEST", f"{route} Content-Length 숫자 아님 → 411")
        r = req(port, "GET", route)
        c.ok(r.status == 405 and r.headers.get("allow") == "POST", f"GET {route} → 405 + Allow: POST")
        r = req(port, "POST", route, struct, {**pdf_headers(None), "Origin": "http://evil.com"})
        c.ok(r.status == 403 and r.code == "FORBIDDEN_HOST", f"{route} Origin evil → 403")
        r = req(port, "POST", route, struct, {**pdf_headers(None), "Host": f"evil.com:{port}"})
        c.ok(r.status == 403, f"{route} Host evil → 403")
        r = req(port, "OPTIONS", route)
        c.ok(r.status == 405 and "access-control-allow-origin" not in r.headers, f"OPTIONS {route} → 405, CORS 없음")
        r = req(port, "POST", route + "?from=1", struct, {**pdf_headers(None), "Origin": f"http://127.0.0.1:{port}"},
                timeout=60)
        c.ok(r.status == 200, f"{route} 같은 출처 Origin → 200 (쿼리가 붙어도 경로 일치)", f"{r.status} {r.code}")

    print("\n[내려받기] POST /api/export")
    t0 = time.monotonic()
    r = req(port, "POST", "/api/export", struct, pdf_headers("한글 이름 파일.pdf"), timeout=200)
    timings["struct.pdf export"] = time.monotonic() - t0
    want_cd = ("attachment; filename=\"extracted.html\"; filename*=UTF-8''"
               + urllib.parse.quote("한글 이름 파일_추출.html", safe=""))
    c.ok(r.status == 200 and r.headers.get("content-type") == "text/html; charset=utf-8"
         and r.headers.get("content-disposition") == want_cd and r.headers.get("cache-control") == "no-store"
         and r.headers.get("x-content-type-options") == "nosniff",
         "한글 이름 파일.pdf → 200 text/html + Content-Disposition filename*(RFC 5987)",
         f"{r.status} {r.headers.get('content-disposition')}")
    c.ok(r.headers.get("x-figures-total") == "1" and r.headers.get("x-figures-omitted") == "0",
         "X-Figures-Total 1 · X-Figures-Omitted 0")
    text = r.body.decode("utf-8", errors="replace")
    stats = {"figures": 1, "figures_omitted": 0}
    c.ok(not cl.html_problems(text, stats) and "<table " in text and "<figure " in text and "<nav " in text,
         "내려받은 HTML: 표·그림·목차, 스크립트·외부 URL 없음, CSP", str(cl.html_problems(text, stats)))
    for raw_name, want in (('a\\b/c:d*e?f"g<h>i|j.pdf', "c_d_e_f_g_h_i_j_추출.html"), ("", "추출한_텍스트.html"),
                           (None, "추출한_텍스트.html"), ("보고서.PDF", "보고서_추출.html")):
        r = req(port, "POST", "/api/export", (pdfs / "ko.pdf").read_bytes(), pdf_headers(raw_name), timeout=60)
        cd = r.headers.get("content-disposition", "")
        got = urllib.parse.unquote(cd.split("filename*=UTF-8''", 1)[-1])
        c.ok(r.status == 200 and got == want, f"X-Filename {raw_name!r} → {want}", f"{r.status} {got}")
    r = req(port, "POST", "/api/export", (pdfs / "xss.pdf").read_bytes(), pdf_headers("<script>x</script>.pdf"))
    text = r.body.decode("utf-8", errors="replace")
    c.ok(r.status == 200 and "<script" not in text.lower() and "&lt;script&gt;alert(1)&lt;/script&gt;" in text
         and not cl.html_problems(text, {"figures": 0, "figures_omitted": 0}),
         "xss.pdf 내려받기: 스크립트 0, 꺾쇠 이스케이프", str(cl.html_problems(text, {"figures": 0, "figures_omitted": 0})))
    cd = r.headers.get("content-disposition", "")
    c.ok("<" not in cd and '"extracted.html"' in cd, "Content-Disposition에 날것의 꺾쇠·따옴표 없음", cd)
    r = req(port, "POST", "/api/export", (pdfs / "figs.pdf").read_bytes(), pdf_headers("figs.pdf"), timeout=200)
    c.ok(r.status == 200 and r.headers.get("x-figures-total") == "42" and r.headers.get("x-figures-omitted") == "10",
         "figs.pdf 내려받기: 그림 42개 중 쪽당 상한으로 10개 생략", f"{r.headers.get('x-figures-total')}/{r.headers.get('x-figures-omitted')}")
    t0 = time.monotonic()
    r = req(port, "POST", "/api/export", longd, pdf_headers("long.pdf"), timeout=200)
    timings["long.pdf export"] = time.monotonic() - t0
    timings["long.pdf export KB"] = len(r.body) / 1024
    c.ok(r.status == 200 and r.body.decode().count('<section class="page"') == 78,
         "long.pdf 내려받기: 잘림 없이 78쪽 전부")
    # 표식 문서: 디스크·로그 점검(보기·내려받기 경로도 문서를 남기지 않아야 한다)
    marker = nonce_pdf(f"Layout marker {NONCE} text")
    r = req(port, "POST", "/api/layout", marker, pdf_headers(f"{NONCE}.pdf"))
    c.ok(r.status == 200 and NONCE in json.dumps(r.json()), "표식 PDF /api/layout → 200")
    r = req(port, "POST", "/api/export", marker, pdf_headers(f"{NONCE}.pdf"))
    c.ok(r.status == 200 and NONCE in r.body.decode(), "표식 PDF /api/export → 200")


def check_layout_slots(c: Checker, port: int, pdfs: Path) -> None:
    print("\n[보기] 처리 자리 LAYOUT_SLOTS=2 → 세 번째는 약 10초 뒤 409 BUSY, 요약은 그동안에도 됨")
    slow = nonce_pdf("SLOWLAYOUT page")
    results: dict[str, Resp] = {}

    def go(k):
        results[k] = req(port, "POST", "/api/layout", slow, pdf_headers(None), timeout=60)
    threads = [threading.Thread(target=go, args=(k,)) for k in ("a", "b")]
    for t in threads:
        t.start()
    time.sleep(0.8)
    body, h = json_body({"text": "보기 처리 중 요약"})
    t0 = time.monotonic()
    r = req(port, "POST", "/api/summarize", body, h)
    c.ok(r.status == 200 and time.monotonic() - t0 < 2, "보기 자리가 다 찼어도 요약은 바로 200")
    r = req(port, "POST", "/api/extract", (pdfs / "ko.pdf").read_bytes(), pdf_headers(None))
    c.ok(r.status == 200, "보기 자리가 다 찼어도 추출은 200")
    t0 = time.monotonic()
    r = req(port, "POST", "/api/export", (pdfs / "ko.pdf").read_bytes(), pdf_headers(None), timeout=60)
    waited = time.monotonic() - t0
    c.ok(r.status == 409 and r.code == "BUSY" and 9.0 <= waited < 12.5,
         "세 번째(내려받기) → LAYOUT_WAIT_S(10초) 뒤 409 BUSY", f"{r.status} {r.code} {waited:.1f}s")
    for t in threads:
        t.join()
    c.ok(all(results.get(k) is not None and results[k].status == 200 for k in ("a", "b")), "앞의 두 요청은 200")
    r = req(port, "POST", "/api/layout", (pdfs / "ko.pdf").read_bytes(), pdf_headers(None))
    c.ok(r.status == 200, "끝난 뒤 다시 → 200 (자리 풀림)")


def run_main_server(c: Checker, port: int, pdfs: Path, logs: Path, scratch: Path) -> str:
    srv = ServerProc(port, logs / "main.log", cwd=scratch)
    timings: dict = {}
    try:
        print("\n[상태] 기본 서버")
        check_health(c, port, tools={"pdftotext": True, "pdfinfo": True}, key=_has_key(), label="기본")
        check_extract_cases(c, port, pdfs, timings)
        check_request_format(c, port, pdfs)
        check_host_origin_static(c, port)
        check_summarize(c, port)
        check_layout_api(c, port, pdfs, timings)
        check_layout_slots(c, port, pdfs)
        check_disconnects(c, port, pdfs)
    finally:
        log_text = srv.stop()
    print("\n  시간: " + ", ".join((f"{n} {t:.0f}" if n.endswith("KB") else f"{n} {t * 1000:.0f}ms")
                                   for n, t in timings.items()))
    return log_text


def _has_key() -> bool:
    sys.path.insert(0, str(APP_DIR))
    try:
        import config
        config.find_api_key()
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        sys.path.remove(str(APP_DIR))


def run_variant_servers(c: Checker, port: int, pdfs: Path, logs: Path, scratch: Path) -> list[str]:
    texts = []
    ko = (pdfs / "ko.pdf").read_bytes()
    empty_home = Path(tempfile.mkdtemp(prefix="home-", dir=scratch))
    nobin = Path(tempfile.mkdtemp(prefix="nobin-", dir=scratch))
    infoonly = Path(tempfile.mkdtemp(prefix="infoonly-", dir=scratch))
    os.symlink("/usr/bin/pdfinfo", infoonly / "pdfinfo")
    base_env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}

    print("\n[변형 1] PDF 도구 없음 · 키 없음 · llm 없음")
    srv = ServerProc(port, logs / "notools.log", mode="nollm", cwd=scratch,
                     env={**base_env, "PATH": str(nobin), "HOME": str(empty_home)})
    try:
        check_health(c, port, tools={"pdftotext": False, "pdfinfo": False}, key=False, label="도구·키 없음",
                     layout_tools={"pdftohtml": False, "pdftoppm": False})
        r = req(port, "POST", "/api/extract", ko, pdf_headers("ko.pdf"))
        c.ok(r.status == 500 and r.code == "TOOL_MISSING", "도구 없음 → 500 TOOL_MISSING", f"{r.status} {r.code}")
        r = req(port, "POST", "/api/extract", b"plain", pdf_headers("x.pdf"))
        c.ok(r.status == 415 and r.code == "NOT_PDF", "도구 없어도 NOT_PDF는 먼저 판정")
        body, h = json_body({"text": "본문"})
        r = req(port, "POST", "/api/summarize", body, h)
        c.ok(r.status == 500 and r.code == "INTERNAL", "llm 없음 → /api/summarize 500 INTERNAL", f"{r.status} {r.code}")
        for route in ("/api/layout", "/api/export"):
            r = req(port, "POST", route, ko, pdf_headers("ko.pdf"))
            c.ok(r.status == 500 and r.code == "INTERNAL", f"pdf_layout 없음 → {route} 500 INTERNAL", f"{r.status} {r.code}")
    finally:
        texts.append(srv.stop())
    c.ok("PDF 도구가 없습니다" in texts[-1] and "API 키가 없습니다" in texts[-1] and "llm.py를 불러오지 못했습니다" in texts[-1]
         and "보기·내려받기 도구가 없습니다" in texts[-1],
         "시작 로그에 도구·키·llm·보기 도구 경고")

    print("\n[변형 2] pdftotext만 없음")
    srv = ServerProc(port, logs / "notext.log", cwd=scratch, env={**base_env, "PATH": str(infoonly)})
    try:
        check_health(c, port, tools={"pdftotext": False, "pdfinfo": True}, key=_has_key(), label="pdftotext 없음",
                     layout_tools={"pdftohtml": False, "pdftoppm": False})
        r = req(port, "POST", "/api/extract", ko, pdf_headers("ko.pdf"))
        c.ok(r.status == 500 and r.code == "TOOL_MISSING", "pdftotext 없음 → TOOL_MISSING", f"{r.status} {r.code}")
    finally:
        texts.append(srv.stop())

    print("\n[변형 3] 추출 제한시간 0.001초")
    srv = ServerProc(port, logs / "timeout.log", extract_timeout=0.001, cwd=scratch)
    try:
        r = req(port, "POST", "/api/extract", (pdfs / "long.pdf").read_bytes(), pdf_headers("long.pdf"))
        c.ok(r.status == 504 and r.code == "EXTRACT_TIMEOUT", "도구 시간 초과 → 504 EXTRACT_TIMEOUT", f"{r.status} {r.code}")
        for route in ("/api/layout", "/api/export"):
            r = req(port, "POST", route, (pdfs / "long.pdf").read_bytes(), pdf_headers("long.pdf"))
            c.ok(r.status == 504 and r.code == "EXTRACT_TIMEOUT", f"{route} 도구 시간 초과 → 504", f"{r.status} {r.code}")
    finally:
        texts.append(srv.stop())

    print("\n[변형 4] pdftohtml만 없음 (SPEC §10.11)")
    nohtml = Path(tempfile.mkdtemp(prefix="nohtml-", dir=scratch))
    for tool in ("pdfinfo", "pdftotext", "pdftoppm"):
        os.symlink(f"/usr/bin/{tool}", nohtml / tool)
    srv = ServerProc(port, logs / "nohtml.log", cwd=scratch, env={**base_env, "PATH": str(nohtml)})
    try:
        check_health(c, port, tools={"pdftotext": True, "pdfinfo": True}, key=_has_key(), label="pdftohtml 없음",
                     layout_tools={"pdftohtml": False, "pdftoppm": True})
        r = req(port, "POST", "/api/extract", ko, pdf_headers("ko.pdf"))
        c.ok(r.status == 200, "pdftohtml 없어도 /api/extract 200", f"{r.status} {r.code}")
        for route in ("/api/layout", "/api/export"):
            r = req(port, "POST", route, ko, pdf_headers("ko.pdf"))
            c.ok(r.status == 500 and r.code == "TOOL_MISSING", f"pdftohtml 없음 → {route} 500 TOOL_MISSING",
                 f"{r.status} {r.code}")
    finally:
        texts.append(srv.stop())
    return texts


def check_logs(c: Checker, logs_text: list[str]) -> None:
    print("\n[로그]")
    joined = "\n".join(logs_text)
    c.ok("Traceback" not in joined, "서버 로그에 트레이스백 없음")
    c.ok("sk-or-v1-" not in joined, "서버 로그에 sk-or-v1- 없음")
    leaked = [n for n in list(mtp.CASES) + list(mtp.LAYOUT_CASES) + ["보고서", "가가가가", "한글 이름"] if n in joined]
    c.ok(not leaked, "서버 로그에 파일 이름 없음", str(leaked))
    titles = [mtp.KO_TITLE, mtp.EN_TITLE, "동호회 운영 규칙", mtp.STRUCT_TITLE, "도시 텃밭 보고서", "두 번째 장",
              "alert(1)", "참여 가구"]
    c.ok(not [t for t in titles if t in joined], "서버 로그에 문서 제목·책갈피·표 셀 없음", str([t for t in titles if t in joined]))
    c.ok("STRAY_FILES" not in joined, "도구가 작업 폴더에 남긴 파일 없음(STRAY_FILES 0)")
    c.ok(not [s for s in SECRET_SENTENCES if s in joined] and "본문입니다" not in joined and "비밀" not in joined,
         "서버 로그에 문서 본문·요약 입력·예외 메시지 없음")
    c.ok(NONCE not in joined, "서버 로그에 표식(업로드 본문·파일 이름·요약 입력) 없음")


def main() -> int:
    ap = argparse.ArgumentParser(description="server.py HTTP 계약 점검 (모델 호출 0회)")
    ap.add_argument("--pdfs", default=str(Path(tempfile.gettempdir()) / "pdf-summary-test-pdfs"),
                    help="시험 PDF 폴더(저장소 밖). 없으면 만든다")
    ap.add_argument("--port", type=int, default=8032, help="점검용 서버 포트(기본 8032)")
    args = ap.parse_args()

    pdfs = Path(args.pdfs).expanduser().resolve()
    if mtp._inside_repo(pdfs):
        print("--pdfs는 저장소 밖이어야 합니다.", file=sys.stderr)
        return 2
    if not all((pdfs / n).is_file() for n in list(mtp.CASES) + list(mtp.LAYOUT_CASES)):
        print(f"시험 PDF 만들기 → {pdfs}")
        rc = subprocess.run([sys.executable, "-B", str(TOOLS_DIR / "make_test_pdfs.py"), "--out", str(pdfs)]).returncode
        if rc != 0:
            return 1
    if not port_free(args.port):
        print(f"포트 {args.port}이 사용 중입니다. --port로 다른 번호를 주세요.", file=sys.stderr)
        return 2
    logs = pdfs / "check_server_logs"
    logs.mkdir(exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="check-server-", dir=pdfs))

    watch = [Path(tempfile.gettempdir()), APP_DIR]
    before = snapshot(watch, pdfs)
    c = Checker()
    t0 = time.monotonic()
    try:
        logs_text = [run_main_server(c, args.port, pdfs, logs, scratch)]
        logs_text += run_variant_servers(c, args.port, pdfs, logs, scratch)
    except RuntimeError as exc:
        print(f"중단: {exc}", file=sys.stderr)
        return 1
    check_logs(c, logs_text)

    print("\n[디스크]")
    after = snapshot(watch, pdfs)
    new = [p for p in after if p not in before or after[p] != before[p]]
    needle = NONCE.encode()
    suspicious = []
    for p in new:
        try:
            if os.path.getsize(p) > 50 * 1024 * 1024:
                continue
            if needle in Path(p).read_bytes():
                suspicious.append(p)
        except OSError:
            continue
    c.ok(not suspicious, "시험 뒤 /tmp·앱 폴더의 새·바뀐 파일에 업로드 내용(표식) 없음", str(suspicious[:5]))
    print(f"  (참고: 시험 중 새로 생기거나 바뀐 파일 {len(new)}개 — 동시에 작업 중인 다른 프로그램 것 포함)")

    print(f"\n결과: 통과 {c.passed} · 실패 {len(c.failed)} · {time.monotonic() - t0:.1f}s · 서버 로그 {logs}")
    for f in c.failed:
        print(f"  실패: {f}")
    return 1 if c.failed else 0


if __name__ == "__main__":
    sys.exit(main())
