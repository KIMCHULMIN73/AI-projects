#!/usr/bin/env python3
"""한눈 PDF 요약 — 프런트엔드 확인용 가짜 서버. 개발 전용(앱은 이 파일을 로드하지 않는다).

SPEC.md §4 계약을 흉내 낸다. 실제 서버(8030)와 겹치지 않게 기본 포트는 8031. 모델·PDF 도구를 부르지 않는다.

    python3 -B tools/mock_server.py [포트]        # 기본 8031, 127.0.0.1에만

정적 파일은 실제 서버의 허용 목록과 같은 넷(/, /index.html, /style.css, /app.js)만, 같은 CSP 헤더로 낸다.

POST /api/extract — 실제 서버처럼 Content-Type·크기·빈 본문·앞 1024바이트 %PDF- 를 검사한 뒤,
업로드한 **파일 이름(X-Filename)** 에 든 낱말로 응답을 고른다(여러 개 섞어 써도 된다):

  err-CODE         그 오류로 실패 (NOT_PDF·ENCRYPTED·BROKEN_PDF·NO_TEXT·TOO_LONG·EXTRACT_TIMEOUT·
                   TOOL_MISSING·EMPTY_FILE·FILE_TOO_LARGE·BAD_REQUEST·INTERNAL·FORBIDDEN_HOST)
  slow-N           N초 늦게 응답 (추출)
  long             58쪽 중 27쪽·60,000자만 담김(truncated)
  mid              약 3만 자(긴 문서 안내가 나오는 길이)
  mixed            글자 없는 쪽 1개(empty_pages=1)
  notitle          title 빈 문자열(화면은 파일 이름을 제목으로)
  nojson           502 + HTML 본문(JSON 아닌 응답)
  badshape         200 + {} (모양이 틀린 성공)
  xss              제목·요약 문자열에 HTML 태그
  longword         띄어쓰기 없는 아주 긴 제목·요약(좁은 화면 넘침 시험)

POST /api/summarize — meta.file_name(추출 때 돌려준 이름)에 든 낱말로 고른다:

  sum-CODE         그 오류로 실패 (NO_KEY·BUSY·RATE_LIMIT·DAILY_LIMIT·TIMEOUT·UPSTREAM·BAD_OUTPUT·TOO_LONG·
                   BAD_REQUEST·INTERNAL). DAILY_LIMIT은 reset_at = 다음 00:00 UTC(= 09:00 KST)
  noreset          DAILY_LIMIT에 reset_at을 넣지 않음
  reset3d          DAILY_LIMIT reset_at = 3일 뒤 00:00 UTC
  once-CODE        이 파일의 첫 요약만 CODE로 실패, 다음부터 성공(다시 시도 시험)
  sumslow-N        N초 늦게 응답 (요약)
  sumnojson        502 + HTML 본문
  sumbadshape      200 + {"one_line": ""} (모양이 틀린 성공)

요약은 동시에 1건만(실제 서버처럼): 진행 중이면 409 BUSY. 클라이언트가 끊어도 정해진 시간까지 잠금을 쥔다
(실제 서버도 이미 시작한 모델 호출을 멈추지 않는다).

── 3단계: 추출한 텍스트 보기·내려받기 (SPEC §10.3·§10.4·§10.6) ──
POST /api/layout?from=&to=&body= · POST /api/export — /api/extract와 같은 검사(Content-Type·크기·빈 본문·%PDF-)를 한 뒤,
layout 요청에는 파일 이름이 없으므로 **PDF 바이트 앞 1024바이트 안의 `%mock:` 줄**에 든 낱말로 시나리오를 고른다
(export는 X-Filename의 낱말도 함께 본다). 예: b"%PDF-1.4\n%mock: pages=12 noout lslow-1\n".

  pages=N          전체 쪽수(기본 2 — struct.pdf처럼 1쪽에 h1·h2·h3·표 3×3·그림, 2쪽에 h1)
  noout            책갈피 없음(outline [], outline_source "none" — 화면이 h1·h2로 목차를 만든다)
  landscape        쪽 크기 842×595
  omit             1쪽 그림 3개가 omitted LIMIT·TIME·FAILED
  figfail          1쪽 figures_failed true
  xss              제목·책갈피·글자·셀·그림 글자에 HTML, 그림 src에 javascript:·http: (그려지면 안 됨)
  longword         띄어쓰기 없는 아주 긴 줄·셀(좁은 화면 넘침 시험)
  lerr-CODE        모든 layout 요청이 CODE로 실패
  lerrat-P-CODE    P쪽이 든 요청은 늘 CODE로 실패 / lonce-P-CODE  첫 번째만 실패(다시 시도 시험)
  lslow-N          layout 요청마다 N초 늦게 / lslowfirst-N  첫 요청만
  lnojson          layout 502 + HTML 본문
  eerr-CODE · eslow-N · enojson   내려받기 실패·지연·JSON 아닌 오류
  eomit-N          내려받기 응답 헤더 X-Figures-Omitted: N

시험용 제어(가짜 서버에만 있음):
  GET  /__mock/log              받은 API 요청 기록(JSON 배열: path·file_name·bytes·status·code)
  POST /__mock/reset            기록·카운터 지우기
  POST /__mock/health?mode=M    /api/health 응답 바꾸기: ok(기본) · notools · nokey · both · nolayout(layout_tools false) · down(500 HTML)
  GET  /__mock/export_name?name=N   이 가짜 서버의 §10.6 파일 이름 규칙 결과(시험이 화면 규칙과 비교)
"""

from __future__ import annotations

import base64
import html
import json
import re
import struct
import sys
import zlib
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = {"/": "index.html", "/index.html": "index.html", "/style.css": "style.css", "/app.js": "app.js",
          "/viewer.html": "viewer.html", "/viewer.css": "viewer.css", "/viewer.js": "viewer.js"}
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_JSON_BYTES = 512 * 1024
MAX_INPUT_CHARS = 60_000

STATUS = {
    "BAD_REQUEST": 400, "EMPTY_FILE": 400, "FILE_TOO_LARGE": 413, "NOT_PDF": 415, "ENCRYPTED": 422,
    "BROKEN_PDF": 422, "NO_TEXT": 422, "TOO_LONG": 413, "EXTRACT_TIMEOUT": 504, "TOOL_MISSING": 500,
    "NO_KEY": 500, "BUSY": 409, "RATE_LIMIT": 429, "DAILY_LIMIT": 429, "TIMEOUT": 504, "UPSTREAM": 502,
    "BAD_OUTPUT": 502, "FORBIDDEN_HOST": 403, "NOT_FOUND": 404, "METHOD_NOT_ALLOWED": 405, "INTERNAL": 500,
}

PARA = ("지역 도서관의 이용자 수는 지난해보다 12% 늘었고, 특히 평일 저녁 시간대 이용이 크게 늘었다. "
        "도서관은 저녁 시간 열람실 운영을 한 시간 연장하고, 어린이 독서 프로그램을 주말로 옮기는 방안을 검토하고 있다. ")

XSS = '<img src=x onerror="window.__xss=1"><script>window.__xss=2</script>'

lock = threading.Lock()
busy = threading.Lock()          # 요약 동시 1건
log: list[dict] = []
counts: dict[str, int] = {}
health_mode = ["ok"]


def next_utc_midnight_ms(days: int = 1) -> int:
    now = datetime.now(timezone.utc)
    t = (now + timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(t.timestamp() * 1000)


def word_after(name: str, prefix: str) -> str | None:
    """'err-NO_TEXT.pdf'에서 prefix 'err-' 뒤의 대문자·밑줄 낱말."""
    i = name.find(prefix)
    if i < 0:
        return None
    rest = name[i + len(prefix):]
    out = ""
    for ch in rest:
        if ch.isupper() or ch == "_":
            out += ch
        else:
            break
    return out or None


def number_after(name: str, prefix: str, default: float) -> float | None:
    i = name.find(prefix)
    if i < 0:
        return None
    digits = ""
    for ch in name[i + len(prefix):]:
        if ch.isdigit() or ch == ".":
            digits += ch
        else:
            break
    try:
        return float(digits)
    except ValueError:
        return default


class Handler(BaseHTTPRequestHandler):
    server_version = "pdf-summary-mock/1"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if "-v" in sys.argv:
            super().log_message(fmt, *args)

    # ── 응답 ──
    def send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def json(self, status: int, obj) -> None:
        self.send(status, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def fail(self, code: str, path: str, name: str = "", nbytes: int = 0, reset_at: int | None = None,
             extra: dict | None = None) -> None:
        err = {"code": code, "message": f"mock {code} (화면은 이 문장을 보여주면 안 됨)"}
        if reset_at is not None:
            err["reset_at"] = reset_at
        self.record(path, name, nbytes, STATUS[code], code, extra)
        self.json(STATUS[code], {"error": err})

    def html_error(self, path: str, name: str = "") -> None:
        self.record(path, name, 0, 502, "HTML")
        self.send(502, b"<html><body><h1>502 Bad Gateway</h1></body></html>", "text/html; charset=utf-8")

    def record(self, path: str, name: str, nbytes: int, status: int, code: str = "", extra: dict | None = None) -> None:
        with lock:
            log.append({"path": path, "file_name": name, "bytes": nbytes, "status": status, "code": code,
                        "t": round(time.time(), 3), **(extra or {})})

    # ── 경로 ──
    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path in STATIC:
            f = ROOT / STATIC[path]
            self.send(200, f.read_bytes(), TYPES[f.suffix], {"Content-Security-Policy": CSP})
        elif path == "/api/health":
            mode = health_mode[0]
            if mode == "down":
                self.send(500, b"<html>down</html>", "text/html; charset=utf-8")
                return
            tools = mode not in ("notools", "both")
            self.json(200, {"ok": True, "app": "pdf-summary", "tools": {"pdftotext": tools, "pdfinfo": True},
                            "key": mode not in ("nokey", "both"),
                            "layout_tools": {"pdftohtml": mode != "nolayout", "pdftoppm": True}})
        elif path == "/__mock/log":
            with lock:
                self.json(200, list(log))
        elif path == "/__mock/export_name":
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            self.json(200, {"name": export_file_name(q.get("name", [""])[0])})
        elif path in ("/api/extract", "/api/summarize", "/api/layout", "/api/export"):
            self.json(405, {"error": {"code": "METHOD_NOT_ALLOWED", "message": "mock"}})
        else:
            self.record(path, "", 0, 404, "NOT_FOUND")      # 그려지면 안 되는 그림 주소 요청 등을 시험이 찾는다
            self.json(404, {"error": {"code": "NOT_FOUND", "message": "mock"}})

    def do_POST(self):
        parts = urllib.parse.urlsplit(self.path)
        if parts.path == "/__mock/reset":
            with lock:
                log.clear()
                counts.clear()
            health_mode[0] = "ok"
            self.json(200, {"ok": True})
        elif parts.path == "/__mock/health":
            health_mode[0] = urllib.parse.parse_qs(parts.query).get("mode", ["ok"])[0]
            self.json(200, {"ok": True, "mode": health_mode[0]})
        elif parts.path == "/api/extract":
            self.extract()
        elif parts.path == "/api/summarize":
            self.summarize()
        elif parts.path == "/api/layout":
            self.layout(urllib.parse.parse_qs(parts.query))
        elif parts.path == "/api/export":
            self.export()
        else:
            self.json(404, {"error": {"code": "NOT_FOUND", "message": "mock"}})

    def read_body(self, limit: int) -> bytes | None:
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            return None
        return self.rfile.read(n) if n else b""

    def extract(self):
        P = "/api/extract"
        name = urllib.parse.unquote(self.headers.get("X-Filename", ""), errors="replace")
        name = name.replace("\\", "/").rsplit("/", 1)[-1][:200]
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/pdf":
            self.fail("BAD_REQUEST", P, name)
            return
        length = self.headers.get("Content-Length")
        if not length or not length.isdigit():
            self.fail("BAD_REQUEST", P, name)
            return
        if int(length) > MAX_FILE_BYTES:
            self.close_connection = True
            self.fail("FILE_TOO_LARGE", P, name, int(length))
            return
        try:
            data = self.read_body(MAX_FILE_BYTES)
        except (ConnectionResetError, BrokenPipeError):
            return
        if not data:
            self.fail("EMPTY_FILE", P, name)
            return
        delay = number_after(name.replace("sumslow-", ""), "slow-", 3)
        if delay:
            time.sleep(delay)
        if b"%PDF-" not in data[:1024]:
            self.fail("NOT_PDF", P, name, len(data))
            return
        code = word_after(name, "err-")
        if code in STATUS:
            self.fail(code, P, name, len(data))
            return
        if "nojson" in name and "sumnojson" not in name:
            self.html_error(P, name)
            return
        if "badshape" in name and "sumbadshape" not in name:
            self.record(P, name, len(data), 200, "BADSHAPE")
            self.json(200, {})
            return

        title = "2026년 지역 도서관 운영 보고서"
        pages, pages_used, empty, truncated = 5, 5, 0, False
        text = (PARA * 40)[:3214]
        if "long" in name and "longword" not in name:
            pages, pages_used, truncated = 58, 27, True
            text = (PARA * 600)[:60_000]
            total = 129_579
        elif "mid" in name:
            pages, pages_used = 14, 14
            text = (PARA * 300)[:30_120]
        if "mixed" in name:
            pages, pages_used, empty = 6, 6, 1
        if "notitle" in name:
            title = ""
        if "xss" in name:
            title = "보고서 " + XSS
        if "longword" in name:
            title = "아주긴제목" * 40
        chars = len(text)
        meta = {"file_name": name, "file_bytes": len(data), "title": title, "pages": pages,
                "pages_used": pages_used, "empty_pages": empty, "chars": chars,
                "chars_total": total if truncated else chars, "truncated": truncated}
        self.record(P, name, len(data), 200)
        self.json(200, {"text": text, "meta": meta})

    # ── 3단계 ──
    def read_pdf(self, P: str) -> tuple[bytes, str, str] | None:
        """extract와 같은 검사. 통과하면 (바이트, 시나리오 글, 파일 이름)."""
        name = urllib.parse.unquote(self.headers.get("X-Filename", ""), errors="replace")
        name = name.replace("\\", "/").rsplit("/", 1)[-1][:200]
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/pdf":
            self.fail("BAD_REQUEST", P, name)
            return None
        length = self.headers.get("Content-Length")
        if not length or not length.isdigit():
            self.fail("BAD_REQUEST", P, name)
            return None
        if int(length) > MAX_FILE_BYTES:
            self.close_connection = True
            self.fail("FILE_TOO_LARGE", P, name, int(length))
            return None
        try:
            data = self.read_body(MAX_FILE_BYTES)
        except (ConnectionResetError, BrokenPipeError):
            return None
        if not data:
            self.fail("EMPTY_FILE", P, name)
            return None
        if b"%PDF-" not in data[:1024]:
            self.fail("NOT_PDF", P, name, len(data))
            return None
        head = data[:1024]
        i = head.find(b"%mock:")
        scen = head[i + 6:].split(b"\n", 1)[0].decode("utf-8", "replace") if i >= 0 else ""
        return data, scen + " " + name, name

    def layout(self, q: dict) -> None:
        P = "/api/layout"
        t0 = time.time()
        got = self.read_pdf(P)
        if got is None:
            return
        data, scen, _ = got
        total = int(number_after(scen, "pages=", 2) or 2)
        try:
            first = int(q["from"][0]) if "from" in q else 1
            last = int(q["to"][0]) if "to" in q else first + LAYOUT_PAGES_PER_REQ - 1
        except ValueError:
            self.fail("BAD_REQUEST", P, "", len(data))
            return
        if first < 1 or first > total or last < first:
            self.fail("BAD_REQUEST", P, "", len(data))
            return
        last = min(last, total, first + LAYOUT_PAGES_PER_REQ - 1)
        body_q = None
        try:
            body_q = float(q["body"][0]) if "body" in q else None
        except ValueError:
            body_q = None
        if body_q is not None and not 4 <= body_q <= 72:
            body_q = None
        with lock:
            counts["layout"] = counts.get("layout", 0) + 1
            nth = counts["layout"]
        delay = number_after(scen, "lslow-", 2) or 0
        if nth == 1:
            delay = max(delay, number_after(scen, "lslowfirst-", 2) or 0)
        if delay:
            time.sleep(delay)
        extra = {"from": first, "to": last, "body": body_q, "t0": round(t0, 3)}
        code = word_after(scen, "lerr-")
        m = re.search(r"l(errat|once)-(\d+)-([A-Z_]+)", scen)
        if m and first <= int(m.group(2)) <= last and m.group(3) in STATUS:
            key = f"once-{first}"
            with lock:
                counts[key] = counts.get(key, 0) + 1
                n_try = counts[key]
            if m.group(1) == "errat" or n_try == 1:
                code = m.group(3)
        if code in STATUS:
            self.fail(code, P, "", len(data), extra=extra)
            return
        if "lnojson" in scen:
            self.html_error(P)
            return
        out = mock_layout(scen, total, first, last, body_q)
        self.record(P, "", len(data), 200, "", extra)
        self.json(200, out)

    def export(self) -> None:
        P = "/api/export"
        got = self.read_pdf(P)
        if got is None:
            return
        data, scen, name = got
        delay = number_after(scen, "eslow-", 2)
        if delay:
            time.sleep(delay)
        code = word_after(scen, "eerr-")
        if code in STATUS:
            self.fail(code, P, name, len(data))
            return
        if "enojson" in scen:
            self.html_error(P, name)
            return
        omitted = int(number_after(scen, "eomit-", 0) or 0)
        total = int(number_after(scen, "pages=", 2) or 2)
        doc_html = mock_export_html(scen, total, name, omitted)
        fname = export_file_name(name)
        self.record(P, name, len(data), 200, "", {"export_name": fname})
        self.send(200, doc_html.encode("utf-8"), "text/html; charset=utf-8", {
            "Content-Disposition": "attachment; filename=\"extracted.html\"; filename*=UTF-8''" + urllib.parse.quote(fname, safe=""),
            "X-Figures-Total": str(max(1, omitted)), "X-Figures-Omitted": str(omitted)})

    def summarize(self):
        P = "/api/summarize"
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self.fail("BAD_REQUEST", P)
            return
        try:
            raw = self.read_body(MAX_JSON_BYTES)
        except (ConnectionResetError, BrokenPipeError):
            return
        if raw is None:
            self.fail("BAD_REQUEST", P)
            return
        try:
            body = json.loads(raw.decode("utf-8"))
            text = body["text"]
            assert isinstance(text, str)
        except Exception:
            self.fail("BAD_REQUEST", P)
            return
        meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
        name = str(meta.get("file_name", ""))[:200]
        if not text.strip():
            self.fail("BAD_REQUEST", P, name, len(raw))
            return
        if len(text) > MAX_INPUT_CHARS:
            self.fail("TOO_LONG", P, name, len(raw))
            return
        if not busy.acquire(blocking=False):
            self.fail("BUSY", P, name, len(raw))
            return
        try:
            delay = number_after(name, "sumslow-", 5) or 0.3
            time.sleep(delay)
            with lock:
                counts[name] = counts.get(name, 0) + 1
                nth = counts[name]
            once = word_after(name, "once-")
            if once in STATUS and nth == 1:
                self.fail(once, P, name, len(raw))
                return
            code = word_after(name, "sum-")
            if code in STATUS:
                reset = None
                if code == "DAILY_LIMIT" and "noreset" not in name:
                    reset = next_utc_midnight_ms(3 if "reset3d" in name else 1)
                self.fail(code, P, name, len(raw), reset)
                return
            if "sumnojson" in name:
                self.html_error(P, name)
                return
            if "sumbadshape" in name:
                self.record(P, name, len(raw), 200, "BADSHAPE")
                self.json(200, {"one_line": "", "overview": "x", "points": []})
                return
            long = len(text) > 20_000
            one = "지역 도서관 이용자가 12% 늘어 저녁 열람실 연장과 주말 어린이 프로그램을 검토한다는 보고서입니다."
            overview = ("지난해보다 도서관 이용자가 12% 늘었고, 특히 평일 저녁 이용이 크게 늘었습니다. "
                        "도서관은 저녁 열람실 운영을 한 시간 연장하는 방안을 검토하고 있습니다. "
                        "어린이 독서 프로그램은 주말로 옮겨 가족 단위 참여를 늘리려 합니다.")
            points = ["이용자 수가 지난해보다 12% 증가", "평일 저녁 시간대 이용이 크게 늘어남",
                      "저녁 열람실 운영 한 시간 연장 검토", "어린이 독서 프로그램을 주말로 이동 검토"]
            if "xss" in name:
                one = "요약 " + XSS
                points = [XSS, "<b>굵게</b> 되면 안 됨", "보통 요점"]
            if "longword" in name:
                one = "띄어쓰기없는아주긴한줄요약" * 30
                points = ["가" * 400, "보통 요점", "세 번째 요점"]
            chunks = (len(text) + 19_999) // 20_000
            out = {"one_line": one, "overview": overview, "points": points,
                   "model": "mock/example-model:free", "calls": chunks + 1 if long else 1,
                   "chunks": chunks, "elapsed_ms": int(delay * 1000)}
            self.record(P, name, len(raw), 200)
            self.json(200, out)
        finally:
            busy.release()


# ── 3단계: 가짜 쪽 구조 (SPEC §10.4 예시 모양) ─────────────────────────

LAYOUT_PAGES_PER_REQ = 10


def export_file_name(file_name: str) -> str:
    """SPEC §10.6 — pdf_layout.export_file_name과 같은 규칙."""
    name = file_name or ""
    if name.lower().endswith(".pdf"):
        name = name[:-4]
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f-\x9f]', "_", name)
    name = re.sub(r"^[\s.]+|[\s.]+$", "", name)[:100]
    return f"{name}_추출.html" if name else "추출한_텍스트.html"


def _png(w: int, h: int) -> bytes:
    """표준 라이브러리로 만든 작은 PNG(파랑 바탕에 주황 막대 셋 — 도표처럼)."""
    rows = []
    for y in range(h):
        row = bytearray([0])
        for x in range(w):
            bar = (x // (w // 4)) in (1, 2, 3) and y > h - (h * ((x // (w // 4)) + 1)) // 5 - 10
            row += bytes((245, 158, 11) if bar else (219, 234, 254))
        rows.append(bytes(row))
    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


PNG_URI = "data:image/png;base64," + base64.b64encode(_png(240, 160)).decode()
BODY_TEXT = ("도시 텃밭이 동네를 바꾸고 있다. 주민들이 함께 가꾸는 작은 밭은 이웃 사이의 대화를 늘린다. "
             "올해는 참여 가구가 지난해보다 크게 늘었고, 구청은 빈 땅을 더 내놓기로 했다. ")


def _text_w(t: str, size: float) -> float:
    return sum(size * (0.92 if ord(c) > 0x1100 else 0.28 if c == " " else 0.55) for c in t)


def _wrap(t: str, size: float, width: float) -> list[str]:
    out, cur = [], ""
    for word in t.split(" "):
        nxt = (cur + " " + word).strip()
        if cur and _text_w(nxt, size) > width:
            out.append(cur)
            cur = word
        else:
            cur = nxt
    if cur:
        out.append(cur)
    return out


def _text_block(bid: str, role: str, text: str, x: float, y: float, size: float, bold: bool, width: float) -> tuple[dict, float]:
    lines, h = [], size * 1.15
    for i, t in enumerate(_wrap(text, size, width)):
        lines.append({"x": x, "y": round(y + i * h, 2), "w": round(min(width, _text_w(t, size)), 2), "h": round(h, 2),
                      "size": size, "bold": bold, "text": t})
    w = max(l["w"] for l in lines)
    return ({"id": bid, "type": "text", "role": role, "x": x, "y": y, "w": w, "h": round(h * len(lines), 2),
             "text": " ".join(l["text"] for l in lines), "lines": lines}, y + h * len(lines))


def mock_page(scen: str, n: int, total: int, body: float) -> dict:
    W, H = (842.0, 595.0) if "landscape" in scen else (612.0, 792.0)
    xss = "xss" in scen
    X = '<img src=x onerror="window.__xss=1"><script>window.__xss=2</script>' if xss else ""
    blocks, y, i = [], 43.17, 0
    width = W - 2 * 33.75

    def add_text(role, text, size, bold, gap=10):
        nonlocal y, i
        b, y2 = _text_block(f"p{n}-b{i}", role, text, 33.75, round(y, 2), size, bold, width)
        blocks.append(b)
        i += 1
        y = y2 + gap

    if n == 1:
        add_text("h1", "1. 도시 텃밭 보고서" + (" " + X if xss else ""), 24.0, True)
        add_text("p", BODY_TEXT + (X if xss else ""), body, False)
        add_text("h2", "1.1 현황", 18.0, True)
        cells = [["구", "텃밭 수", "참여 가구"], ["마포", "12", "340"], ["은평", "8", "210"]]
        if xss:
            cells[1][0] = '<b>마포</b>"\'&'
        if "longword" in scen:
            cells[2][0] = "띄어쓰기없는아주긴셀" * 6
        blocks.append({"id": f"p{n}-b{i}", "type": "table", "x": 37.5, "y": round(y, 2), "w": 200.0, "h": 51.3, "size": body,
                       "cols": [37.5, 90.0, 150.0],
                       "rows": [[{"text": t, "bold": r == 0} for t in row] for r, row in enumerate(cells)]})
        i += 1
        y += 64
        add_text("h3", "그림 1", 14.0, True, 6)
        figs = [("data", None)]
        if "omit" in scen:
            figs = [(None, "LIMIT"), (None, "TIME"), (None, "FAILED")]
        if xss:
            figs = [("data", None), ("javascript:window.__xss=3", None), ("http://127.0.0.1:8031/evil.png", None)]
        for k, (src, om) in enumerate(figs):
            s = PNG_URI if src == "data" else src
            blocks.append({"id": f"p{n}-b{i}", "type": "figure", "x": 33.75 + k * 190, "y": round(y, 2), "w": 180.0, "h": 120.0,
                           "src": s, "text": ("도표 안 글자 12%" + (" " + X if xss else "")) if k == 0 else "", "omitted": om})
            i += 1
        y += 132
        add_text("p", "그림 아래 문단이다. " + BODY_TEXT, body, False)
        if "longword" in scen:
            add_text("p", "띄어쓰기없는아주긴줄" * 30, body, False)
    else:
        if n % 3 != 0:
            add_text("h1", f"{n}. 제{n}장 이야기", 24.0, True)
        else:
            add_text("h2", f"{n - 1}.1 세부 내용", 18.0, True)
        for k in range(4):
            add_text("p", f"{n}쪽 {k + 1}번째 문단. " + BODY_TEXT, body, False)
    return {"number": n, "width": W, "height": H, "figures_failed": n == 1 and "figfail" in scen,
            "background_skipped": 0, "blocks": blocks}


def mock_outline(scen: str, total: int) -> list[dict]:
    if "noout" in scen:
        return []
    X = " <script>window.__xss=4</script>" if "xss" in scen else ""
    out = [{"title": "1. 도시 텃밭 보고서" + X, "page": 1, "level": 1}, {"title": "1.1 현황", "page": 1, "level": 2},
           {"title": "그림 1", "page": 1, "level": 3}]
    for n in range(2, total + 1):
        if n % 3 != 0:
            out.append({"title": f"{n}. 제{n}장 이야기" if n > 2 else "2. 두 번째 장", "page": n, "level": 1})
    return out


def mock_layout(scen: str, total: int, first: int, last: int, body_q: float | None) -> dict:
    body = body_q or 12.0
    pages = [mock_page(scen, n, total, body) for n in range(first, last + 1)]
    figs = [b for p in pages for b in p["blocks"] if b["type"] == "figure"]
    outline = mock_outline(scen, total)
    size = [842.0, 595.0] if "landscape" in scen else [612.0, 792.0]
    title = "구조 시험 문서" + (' <img src=x onerror="window.__xss=5">' if "xss" in scen else "")
    return {"doc": {"pages": total, "title": "" if "notitle" in scen else title, "page_sizes": [size] * total,
                    "outline": outline, "outline_source": "bookmarks" if outline else "none"},
            "from": first, "to": last, "body_size": body, "pages": pages,
            "notes": {"figures": len(figs), "figures_omitted": sum(1 for f in figs if f["omitted"])}}


def mock_export_html(scen: str, total: int, name: str, omitted: int) -> str:
    """SPEC §10.6 모양의 짧은 HTML(스크립트 없음). 실제 내용은 서버가 만든다 — 화면은 받은 그대로 저장만 한다."""
    e = lambda t: html.escape(t, quote=True)  # noqa: E731
    lay = mock_layout(scen, total, 1, min(total, 3), None)
    parts = ['<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
             "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src data:; style-src 'unsafe-inline'\">\n"
             f"<title>{e(lay['doc']['title'] or name)} — 추출한 텍스트</title>\n</head>\n<body>\n"
             f"<header><h1>{e(lay['doc']['title'] or name)}</h1><p class=\"src\">원본: {e(name)} · {total}쪽</p>"
             + (f'<p class="note warn">그림 {omitted}개는 파일 크기·시간 제한으로 넣지 않았습니다.</p>' if omitted else "")
             + "</header>\n<main>\n"]
    for p in lay["pages"]:
        parts.append(f'<section class="page" id="p{p["number"]}" aria-label="{p["number"]}쪽"><p class="page-no">{p["number"]}쪽</p>\n')
        for b in p["blocks"]:
            if b["type"] == "text":
                tag = {"h1": "h2", "h2": "h3", "h3": "h4"}.get(b["role"], "p")
                parts.append(f'<{tag} id="{b["id"]}">{e(b["text"])}</{tag}>\n')
            elif b["type"] == "table":
                rows = "".join("<tr>" + "".join(f"<td>{e(c['text'])}</td>" for c in r) + "</tr>" for r in b["rows"])
                parts.append(f'<table id="{b["id"]}"><tbody>{rows}</tbody></table>\n')
            elif b["src"] and b["src"].startswith("data:image/png;base64,"):
                parts.append(f'<figure id="{b["id"]}"><img src="{b["src"]}" alt="{p["number"]}쪽 그림" width="240" height="160"></figure>\n')
        parts.append("</section>\n")
    parts.append("</main>\n</body>\n</html>\n")
    return "".join(parts)


def main() -> None:
    port = int(next((a for a in sys.argv[1:] if a.isdigit()), 8031))
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    print(f"가짜 서버: http://127.0.0.1:{port}/  (Ctrl+C로 종료)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
