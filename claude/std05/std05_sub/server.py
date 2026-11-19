"""한눈 PDF 요약 서버 — 정적 파일 7개 + /api/health · /api/extract · /api/summarize · /api/layout · /api/export.

    python3 -B server.py       # http://127.0.0.1:8030/  (보통은 ./run.sh)

- 127.0.0.1:8030 고정. 외부에 열지 않는다.
- 정적 파일은 허용 목록(STATIC)과 경로가 정확히 같을 때만. 그 밖(config.py, .env, tools/ …)은 404.
- Host가 127.0.0.1:8030 / localhost:8030이 아니면 403(DNS 리바인딩 방지).
  POST는 Origin이 있으면 이 두 주소만 허용(`null` 포함 그 밖은 403). CORS 헤더는 내보내지 않는다.
- 업로드 PDF는 메모리에서만 다룬다(pdf_extract가 도구의 stdin으로 넘긴다). 디스크·로그에 남기지 않는다.
- 로그에는 파일 이름·제목·본문·키를 찍지 않는다. 모든 로그는 config.redact를 거친다.
- `pdf_extract`·`llm`은 모듈 속성으로 부른다(`llm.summarize(...)`) — tools/check_server.py가 가짜로 바꿔 끼운다.
  `llm.py`가 없거나 import에 실패해도 서버는 뜨고, /api/summarize만 500 INTERNAL.
- 요약은 한 번에 1건(_summarize_lock). 화면에서 취소·새로고침해 연결이 끊기면 llm.summarize가 should_stop으로
  알아채 더 부르지 않고 잠금을 놓는다(로그 CLIENT_GONE). 다른 요청은 잠금을 BUSY_WAIT_S만큼 기다린 뒤 BUSY.
- (3단계) /api/layout·/api/export는 모델을 부르지 않는다. pdf_layout이 poppler 도구로 쪽 구조·내려받을 HTML을 만든다.
  요약 잠금과 별개의 처리 자리 LAYOUT_SLOTS개(LAYOUT_WAIT_S 기다려도 없으면 BUSY). 문서를 들고 있지 않는다(캐시 없음).
  `pdf_layout` import가 실패해도 서버는 뜨고 두 경로만 500 INTERNAL.

계약: SPEC.md §4(HTTP)·§5(모듈)·§8(보안)·§10.3(보기·내려받기).
"""

from __future__ import annotations

import json
import select
import shutil
import signal
import socket
import sys
import threading
import time
import unicodedata
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import config
import pdf_extract

try:
    import llm
    _LLM_IMPORT_ERROR = ""
except Exception as _exc:  # noqa: BLE001 — llm.py가 없거나 깨져도 서버는 떠야 한다(SPEC §5)
    llm = None
    _LLM_IMPORT_ERROR = type(_exc).__name__

try:
    import pdf_layout
    _LAYOUT_IMPORT_ERROR = ""
except Exception as _exc:  # noqa: BLE001 — 보기·내려받기만 못 쓰고 추출·요약은 된다(SPEC §10.5)
    pdf_layout = None
    _LAYOUT_IMPORT_ERROR = type(_exc).__name__

HOST = "127.0.0.1"
PORT = 8030
ROOT = Path(__file__).resolve().parent
SERVER_NAME = "pdf-summary/1"   # run.sh가 "이 앱이 이미 떠 있는지"를 Server 헤더 앞부분(pdf-summary)으로 가린다
APP_NAME = "pdf-summary"

ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
ALLOWED_ORIGINS = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}

MAX_FILE_BYTES = 20 * 1024 * 1024   # 20 MiB. app.js에도 같은 숫자가 있다(업로드 전 미리 검사)
MAX_JSON_BYTES = 512 * 1024         # 60,000자 × UTF-8 3바이트 + JSON 이스케이프 여유
BODY_READ_TIMEOUT_S = 15            # 본문을 읽는 동안 recv 한 번의 제한(느린 클라이언트가 스레드를 붙잡지 않게)
META_STR_MAX = 200
FILE_NAME_MAX = 200
LINGER_S = 2                        # 본문을 읽지 않고 거절한 뒤 연결을 닫기 전, 남은 본문을 버리는 시간
BUSY_WAIT_S = 2.0                   # 요약 잠금을 이만큼 기다린 뒤에도 못 얻으면 BUSY. 화면에서 [요약 취소] 직후
                                    # 다시 눌렀을 때, 앞 요청이 연결 끊김을 알아채고 잠금을 놓을 시간(llm.STOP_POLL_S의 몇 배)
LAYOUT_SLOTS = 2                    # /api/layout·/api/export 동시 처리 수(요약 잠금과 별개, Pi CPU 보호)
LAYOUT_WAIT_S = 10.0                # 자리를 이만큼 기다려도 없으면 BUSY
LAYOUT_TOOLS = ("pdftohtml", "pdftoppm")
PDF_ROUTES = ("/api/extract", "/api/layout", "/api/export")

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/viewer.html": ("viewer.html", "text/html; charset=utf-8"),
    "/viewer.css": ("viewer.css", "text/css; charset=utf-8"),
    "/viewer.js": ("viewer.js", "text/javascript; charset=utf-8"),
}
API_METHODS = {"/api/health": "GET, HEAD", "/api/extract": "POST", "/api/summarize": "POST",
               "/api/layout": "POST", "/api/export": "POST"}
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

# 오류 코드 → (기본 HTTP 상태, 개발자용 짧은 설명). 화면은 message가 아니라 code로 문구를 정한다(SPEC §4.1).
ERRORS = {
    "BAD_REQUEST": (400, "요청 형식이 올바르지 않습니다."),
    "EMPTY_FILE": (400, "빈 파일입니다."),
    "FILE_TOO_LARGE": (413, f"파일이 {MAX_FILE_BYTES} 바이트를 넘습니다."),
    "NOT_PDF": (415, "PDF 파일이 아닙니다(%PDF- 머리 없음)."),
    "ENCRYPTED": (422, "열기 암호가 걸린 PDF입니다."),
    "BROKEN_PDF": (422, "PDF를 열 수 없습니다."),
    "NO_TEXT": (422, "PDF에서 글자를 찾지 못했습니다(스캔본일 수 있음)."),
    "TOO_LONG": (413, "문서가 너무 깁니다."),
    "EXTRACT_TIMEOUT": (504, "PDF 추출 시간이 초과되었습니다."),
    "TOOL_MISSING": (500, "pdftotext/pdfinfo가 없습니다: sudo apt install poppler-utils"),
    "NO_KEY": (500, "API 키가 없습니다: python3 -B config.py 로 진단하세요."),
    "BUSY": (409, "다른 요약이 진행 중입니다."),
    "RATE_LIMIT": (429, "업스트림 요청 한도(일시적)."),
    "DAILY_LIMIT": (429, "무료 모델 하루 한도를 모두 썼습니다."),
    "TIMEOUT": (504, "요약 시간이 초과되었습니다."),
    "UPSTREAM": (502, "업스트림 오류."),
    "BAD_OUTPUT": (502, "모델 응답을 해석하지 못했습니다."),
    "FORBIDDEN_HOST": (403, "허용되지 않은 Host 또는 Origin입니다. http://127.0.0.1:8030/ 으로 접속하세요."),
    "NOT_FOUND": (404, "없는 경로입니다."),
    "METHOD_NOT_ALLOWED": (405, "허용되지 않는 메서드입니다."),
    "INTERNAL": (500, "서버 내부 오류입니다."),
}
SUMMARIZE_CODES = {"NO_KEY", "RATE_LIMIT", "DAILY_LIMIT", "TIMEOUT", "UPSTREAM", "BAD_OUTPUT", "TOO_LONG"}

# 동시 요약은 1건만(두 탭에서 동시에 눌러 하루 한도를 두 배로 쓰는 것을 막는다)
_summarize_lock = threading.Lock()
# 보기·내려받기 처리 자리(poppler 도구를 여러 번 띄우는 무거운 작업)
_layout_slots = threading.BoundedSemaphore(LAYOUT_SLOTS)


class ApiError(Exception):
    def __init__(self, code: str, status: int | None = None, message: str | None = None,
                 reset_at: int | None = None, log: str = ""):
        super().__init__(code)
        if code not in ERRORS:
            code = "INTERNAL"
        default_status, default_msg = ERRORS[code]
        self.code = code
        self.status = status or default_status
        self.message = message or default_msg
        self.reset_at = reset_at
        self.log = log


class ClientGone(Exception):
    """본문이 Content-Length만큼 오지 않았다(끊김·시간 초과)."""


class RequesterLeft(Exception):
    """본문은 다 받았지만 처리 중에 요청자가 떠났다(보기·내려받기). 응답하지 않고 연결을 닫는다."""


def log(line: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {config.redact(line)}", file=sys.stderr, flush=True)


def _has_surrogate(s: str) -> bool:
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in s)


def clean_file_name(raw: str | None) -> str:
    """X-Filename(퍼센트 인코딩) → 화면에 돌려줄 이름. 실패하면 빈 문자열. 로그에 쓰지 않는다."""
    if not raw:
        return ""
    try:
        name = urllib.parse.unquote(raw, encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError):
        return ""
    name = name.replace("\\", "/").rsplit("/", 1)[-1]                  # 경로 구분자 앞부분은 버린다
    name = "".join(ch for ch in name if unicodedata.category(ch) != "Cc")  # 제어문자 제거
    if _has_surrogate(name):
        return ""
    return unicodedata.normalize("NFC", name).strip()[:FILE_NAME_MAX]


def clean_meta(meta: object) -> dict:
    """/api/summarize의 meta → llm.summarize에 넘길 새 dict(SPEC §5.2 키만). 틀린 필드는 기본값."""
    src = meta if isinstance(meta, dict) else {}

    def text(key: str) -> str:
        v = src.get(key)
        if not isinstance(v, str) or _has_surrogate(v):
            return ""
        return "".join(ch for ch in v if unicodedata.category(ch) != "Cc")[:META_STR_MAX]

    def count(key: str) -> int:
        v = src.get(key)
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 1_000_000:
            return 0
        return v

    truncated = src.get("truncated")
    return {
        "title": text("title"),
        "file_name": text("file_name"),
        "pages": count("pages"),
        "pages_used": count("pages_used"),
        "truncated": truncated if isinstance(truncated, bool) else False,
    }


def validate_summary_request(body: object) -> tuple[str, dict]:
    if not isinstance(body, dict):
        raise ApiError("BAD_REQUEST", message="요청 본문은 JSON 객체여야 합니다.")
    text = body.get("text")
    if not isinstance(text, str):
        raise ApiError("BAD_REQUEST", message="text 필드(문자열)가 필요합니다.")
    if _has_surrogate(text):
        raise ApiError("BAD_REQUEST", message="짝 없는 서로게이트가 섞여 있습니다.")
    text = text.strip()
    if not text:
        raise ApiError("BAD_REQUEST", message="text가 비어 있습니다.")
    if len(text) > pdf_extract.MAX_INPUT_CHARS:
        raise ApiError("TOO_LONG", message=f"text가 {pdf_extract.MAX_INPUT_CHARS}자를 넘습니다.")
    return text, clean_meta(body.get("meta"))


def _summary_response(result: object, elapsed_ms: int) -> dict:
    """llm.summarize 반환값 → 응답. 계약(SPEC §4.2)의 키만 내보낸다. 형식이 틀리면 INTERNAL."""
    try:
        out = {
            "one_line": result["one_line"],
            "overview": result["overview"],
            "points": result["points"],
            "model": result["model"],
            "calls": result["calls"],
            "chunks": result["chunks"],
            "elapsed_ms": elapsed_ms,
        }
    except (TypeError, KeyError):
        raise ApiError("INTERNAL", log="llm.summarize 반환 형식 위반(키 누락)") from None
    if not (isinstance(out["one_line"], str) and isinstance(out["overview"], str)
            and isinstance(out["points"], list) and all(isinstance(p, str) for p in out["points"])
            and isinstance(out["model"], str)
            and isinstance(out["calls"], int) and isinstance(out["chunks"], int)):
        raise ApiError("INTERNAL", log="llm.summarize 반환 형식 위반(타입)")
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = SERVER_NAME
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 60  # 연결이 놀고 있을 때 스레드를 무한정 붙잡지 않게

    def version_string(self):  # 파이썬 버전을 알리지 않는다
        return SERVER_NAME

    # ── 로그: 요청마다 한 줄(시각·메서드·경로·상태·코드·본문 바이트·ms·[calls·model]) ──
    def log_message(self, fmt, *args):
        log(str(fmt % args)[:200])

    def log_request(self, code="-", size="-"):
        pass  # _finish_log가 대신 찍는다

    def _begin(self) -> None:
        self._t0 = time.monotonic()
        self._status = "-"
        self._code = ""
        self._body_bytes = 0
        self._note = ""
        self._unread_body = False

    def _finish_log(self) -> None:
        ms = int((time.monotonic() - self._t0) * 1000)
        path = self.path.split("?", 1)[0][:100]
        parts = [self.command, path, str(self._status), self._code or "-", f"{self._body_bytes}B", f"{ms}ms"]
        if self._note:
            parts.append(self._note)
        log(" ".join(parts))

    # ── 응답 ──
    def _send(self, status: int, body: bytes, content_type: str, *, head_only: bool = False,
              extra: dict | None = None) -> None:
        headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        }
        headers.update(extra or {})
        if self.close_connection:
            headers["Connection"] = "close"
        self._status = status
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _json(self, status: int, obj: dict, **kw) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", **kw)

    def _error(self, err: ApiError, **kw) -> None:
        self._code = err.code
        if err.log:
            self._note = f"({err.log[:200]})"
        error = {"code": err.code, "message": err.message}
        if err.reset_at is not None:
            error["reset_at"] = err.reset_at
        self._json(err.status, {"error": error}, **kw)

    def send_error(self, code, message=None, explain=None):
        """http.server가 요청 줄·헤더를 해석하지 못했을 때(400·414·431·501 …)도 JSON 오류로."""
        self.close_connection = True
        if not hasattr(self, "_t0"):
            self._begin()
        if code == 501:   # 정의하지 않은 메서드(FOO 등)
            err = ApiError("METHOD_NOT_ALLOWED")
        elif 400 <= code < 500:
            err = ApiError("BAD_REQUEST", status=code)
        else:
            err = ApiError("INTERNAL")
        try:
            self._error(err, head_only=(getattr(self, "command", "") == "HEAD"))
        except OSError:
            pass
        log(f"요청 해석 실패 {code} → {err.status} {err.code}")

    # ── 공통 검사 ──
    def _route(self) -> str:
        return self.path.split("?", 1)[0].split("#", 1)[0]

    def _host_ok(self) -> bool:
        return (self.headers.get("Host") or "").strip().lower() in ALLOWED_HOSTS

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin.strip().lower() in ALLOWED_ORIGINS

    def _content_length(self) -> int | None:
        raw = self.headers.get("Content-Length")
        if raw is None or not raw.strip().isdigit():
            return None
        return int(raw.strip())

    def _post_precheck(self) -> ApiError | None:
        """본문을 읽기 전에 알 수 있는 거절 사유. do_POST와 handle_expect_100이 같이 쓴다."""
        if not self._host_ok() or not self._origin_ok():
            return ApiError("FORBIDDEN_HOST")
        route = self._route()
        if route not in ("/api/summarize", *PDF_ROUTES):
            if route in STATIC or route in API_METHODS:
                self._allow = API_METHODS.get(route, "GET, HEAD")
                return ApiError("METHOD_NOT_ALLOWED")
            return ApiError("NOT_FOUND")
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        want = "application/pdf" if route in PDF_ROUTES else "application/json"
        if ctype != want:
            return ApiError("BAD_REQUEST", status=415, message=f"Content-Type은 {want}이어야 합니다.")
        if self.headers.get("Transfer-Encoding"):
            return ApiError("BAD_REQUEST", status=411, message="Content-Length가 있는 본문만 받습니다.")
        length = self._content_length()
        if length is None:
            return ApiError("BAD_REQUEST", status=411, message="Content-Length가 필요합니다.")
        if route in PDF_ROUTES:
            if length > MAX_FILE_BYTES:
                return ApiError("FILE_TOO_LARGE")
            if length == 0:
                return ApiError("EMPTY_FILE")
        elif length > MAX_JSON_BYTES:
            return ApiError("BAD_REQUEST", status=413, message=f"본문이 {MAX_JSON_BYTES} 바이트를 넘습니다.")
        return None

    def handle_expect_100(self):
        # curl 등은 큰 본문 앞에 "Expect: 100-continue"를 보낸다. 어차피 거절할 요청이면
        # 100 Continue를 보내지 않고 바로 오류를 돌려줘서 본문 전송 자체를 막는다.
        if self.command == "POST" and self._post_precheck() is not None:
            return True
        return super().handle_expect_100()

    def _read_body(self, length: int) -> bytes:
        """정확히 length 바이트를 읽는다. 끊기면 ClientGone."""
        prev = self.connection.gettimeout()
        self.connection.settimeout(BODY_READ_TIMEOUT_S)
        chunks: list[bytes] = []
        got = 0
        try:
            while got < length:
                chunk = self.rfile.read(min(1 << 20, length - got))
                if not chunk:
                    break
                chunks.append(chunk)
                got += len(chunk)
        except (OSError, ValueError):
            raise ClientGone() from None
        finally:
            try:
                self.connection.settimeout(prev)
            except OSError:
                pass
        self._body_bytes = got
        if got != length:
            raise ClientGone()
        return b"".join(chunks)

    def _client_gone(self) -> bool:
        """요약을 기다리던 브라우저가 떠났는지(취소·새로고침·창 닫기 → 연결이 닫힘). llm.summarize가 수시로 부른다.

        읽을 것이 있다고 나오는데 엿본 결과가 빈 바이트면 상대가 연결을 닫은 것이다(본문은 이미 다 읽었다).
        """
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            if not readable:
                return False
            return self.connection.recv(1, socket.MSG_PEEK) == b""
        except (OSError, ValueError):
            return True

    def _linger(self) -> None:
        """본문을 읽지 않고 거절한 연결을 닫기 전, 들어오는 본문을 잠깐 버린다.

        읽지 않은 데이터가 남은 소켓을 바로 닫으면 커널이 RST를 보내 클라이언트가 413 응답을
        받기 전에 '연결 끊김'만 볼 수 있다. 쓰기 쪽을 먼저 닫고 LINGER_S 동안만 버린다(처리하지 않음).
        """
        try:
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_WR)
            deadline = time.monotonic() + LINGER_S
            while (left := deadline - time.monotonic()) > 0:
                self.connection.settimeout(left)
                if not self.connection.recv(1 << 16):
                    break
        except OSError:
            pass

    # ── GET / HEAD ──
    def do_GET(self):
        self._handle(self._get, head_only=False)

    def do_HEAD(self):
        self._handle(self._get, head_only=True)

    def _get(self, *, head_only: bool) -> None:
        if not self._host_ok():
            raise ApiError("FORBIDDEN_HOST")
        route = self._route()
        if route == "/api/health":
            return self._json(200, self._health(), head_only=head_only)
        if route in API_METHODS:
            self._allow = API_METHODS[route]
            raise ApiError("METHOD_NOT_ALLOWED")
        # 허용 목록과 정확히 같을 때만(쿼리 문자열은 뗀다). /..%2f, //, 대소문자 변형은 모두 404.
        entry = STATIC.get(route)
        if entry is None:
            raise ApiError("NOT_FOUND")
        name, ctype = entry
        try:
            data = (ROOT / name).read_bytes()  # 매번 읽는다: 작고, 프런트가 고치는 즉시 반영된다
        except OSError:
            raise ApiError("NOT_FOUND", log=f"{name} 없음") from None
        self._send(200, data, ctype, head_only=head_only,
                   extra={"Cache-Control": "no-cache", "Content-Security-Policy": CSP})

    def _health(self) -> dict:
        try:
            config.find_api_key()
            key = True
        except Exception:  # noqa: BLE001 — 있음/없음만 알린다(값·길이·출처는 내보내지 않음)
            key = False
        return {"ok": True, "app": APP_NAME, "tools": pdf_extract.check_tools(), "key": key,
                "layout_tools": {name: shutil.which(name) is not None for name in LAYOUT_TOOLS}}

    # ── POST ──
    def do_POST(self):
        self._handle(self._post, head_only=False)

    def _post(self, *, head_only: bool) -> None:
        err = self._post_precheck()
        if err is not None:
            # 본문을 읽지 않았으므로 이 연결은 더 쓰지 않는다.
            self.close_connection = True
            self._unread_body = (self._content_length() or 0) > 0
            raise err
        route = self._route()
        data = self._read_body(self._content_length() or 0)
        if route == "/api/extract":
            self._extract(data)
        elif route == "/api/layout":
            self._layout(data)
        elif route == "/api/export":
            self._export(data)
        else:
            self._summarize(data)

    def _extract(self, data: bytes) -> None:
        file_name = clean_file_name(self.headers.get("X-Filename"))
        try:
            info = pdf_extract.extract(data)
        except pdf_extract.ExtractError as exc:
            raise ApiError(exc.code, log=exc.log) from None
        meta = {
            "file_name": file_name,
            "file_bytes": len(data),
            "title": info["title"],
            "pages": info["pages"],
            "pages_used": info["pages_used"],
            "empty_pages": info["empty_pages"],
            "chars": info["chars"],
            "chars_total": info["chars_total"],
            "truncated": info["truncated"],
        }
        self._note = (f"pages={meta['pages']} used={meta['pages_used']} empty={meta['empty_pages']} "
                      f"chars={meta['chars']}/{meta['chars_total']}")
        self._json(200, {"text": info["text"], "meta": meta})

    def _summarize(self, raw: bytes) -> None:
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ApiError("BAD_REQUEST", message="본문이 올바른 JSON이 아닙니다.") from None
        text, meta = validate_summary_request(body)
        if llm is None:
            raise ApiError("INTERNAL", log=f"llm 모듈 없음({_LLM_IMPORT_ERROR})")
        if not _summarize_lock.acquire(timeout=BUSY_WAIT_S):
            raise ApiError("BUSY")
        started = time.monotonic()
        try:
            # 브라우저가 떠나면(취소·새로고침) llm이 다음 호출을 하지 않고 CANCELLED로 끝낸다 → 잠금이 바로 풀린다.
            result = llm.summarize(text, meta, should_stop=self._client_gone)
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, getattr(llm, "SummarizeError", ())) and getattr(exc, "code", "") == "CANCELLED":
                # 응답을 받을 사람이 없다. 쓰지 않고 연결을 닫는다(로그에는 CLIENT_GONE).
                self.close_connection = True
                self._code = "CLIENT_GONE"
                self._note = f"({str(getattr(exc, 'log', ''))[:200]})"
                return
            if isinstance(exc, getattr(llm, "SummarizeError", ())) and getattr(exc, "code", "") in SUMMARIZE_CODES:
                reset_at = getattr(exc, "reset_at", None)
                if exc.code != "DAILY_LIMIT" or not isinstance(reset_at, int) or isinstance(reset_at, bool):
                    reset_at = None
                raise ApiError(exc.code, reset_at=reset_at, log=str(getattr(exc, "log", ""))) from None
            raise ApiError("INTERNAL", log=f"llm.summarize 예외 {type(exc).__name__}") from None
        finally:
            _summarize_lock.release()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        out = _summary_response(result, elapsed_ms)
        trail = result.get("trail") if isinstance(result, dict) else None   # 시도 기록(본문 없음) — 로그에만
        self._note = f"calls={out['calls']} chunks={out['chunks']} model={out['model'][:80]}" + (
            f" [{trail[:300]}]" if isinstance(trail, str) and trail else "")
        self._json(200, out)

    # ── 보기·내려받기 (SPEC §10.3) ──
    def _layout_query(self) -> tuple[int | None, int | None, float | None]:
        """?from=F&to=T&body=B → (from, to, body). 정수가 아닌 from·to는 400, body는 틀리면 무시(서버가 계산)."""
        query = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "", keep_blank_values=True)

        def page_no(key: str) -> int | None:
            vals = query.get(key)
            if not vals:
                return None
            v = vals[0].strip()
            if not v or not (v.isascii() and (v.isdigit() or (v[0] in "+-" and v[1:].isdigit()))) or len(v) > 9:
                raise ApiError("BAD_REQUEST", message=f"{key}는 정수여야 합니다.")
            return int(v)

        first, last = page_no("from"), page_no("to")
        body = None
        raw = (query.get("body") or [""])[0].strip()
        if raw:
            try:
                b = float(raw)
            except ValueError:
                b = None
            if b is not None and b == b and 4.0 <= b <= 72.0:
                body = b
        return first, last, body

    def _with_slot(self, fn):
        if pdf_layout is None:
            raise ApiError("INTERNAL", log=f"pdf_layout 모듈 없음({_LAYOUT_IMPORT_ERROR})")
        if not _layout_slots.acquire(timeout=LAYOUT_WAIT_S):
            raise ApiError("BUSY", message="다른 보기·내려받기 처리가 진행 중입니다.")
        try:
            return fn()
        except pdf_extract.ExtractError as exc:
            if exc.code == "CLIENT_GONE":
                # 보기 창·[다른 파일]·새로고침으로 요청자가 떠났다 — 남은 쪽을 만들지 않고 자리를 놓는다(응답할 곳이 없다).
                raise RequesterLeft() from None
            code = "BAD_REQUEST" if exc.code == "BAD_RANGE" else exc.code
            if code == "NO_TEXT":     # 계약상 내지 않는다(글자가 적어도 쪽·그림은 보여 준다) — 생기면 버그
                code = "INTERNAL"
            raise ApiError(code, log=getattr(exc, "log", "")) from None
        finally:
            _layout_slots.release()

    def _layout(self, data: bytes) -> None:
        first, last, body = self._layout_query()
        result = self._with_slot(lambda: pdf_layout.layout(data, first if first is not None else 1, last, body,
                                                           should_stop=self._client_gone))
        self._note = (f"from={result['from']} to={result['to']} figs={result['notes']['figures']} "
                      f"omitted={result['notes']['figures_omitted']}")
        self._json(200, result)

    def _export(self, data: bytes) -> None:
        file_name = clean_file_name(self.headers.get("X-Filename"))
        text, stats = self._with_slot(lambda: pdf_layout.export_html(data, file_name, should_stop=self._client_gone))
        out_name = pdf_layout.export_file_name(file_name)
        disposition = ('attachment; filename="extracted.html"; filename*=UTF-8\'\''
                       + urllib.parse.quote(out_name, safe=""))
        self._note = f"pages={stats['pages']} figs={stats['figures']} omitted={stats['figures_omitted']}"
        self._send(200, text.encode("utf-8"), "text/html; charset=utf-8",
                   extra={"Content-Disposition": disposition,
                          "X-Figures-Total": str(stats["figures"]),
                          "X-Figures-Omitted": str(stats["figures_omitted"])})

    # ── 그 밖의 메서드 ──
    def _other(self, *, head_only: bool) -> None:
        self.close_connection = True
        if not self._host_ok():
            raise ApiError("FORBIDDEN_HOST")
        route = self._route()
        if route in STATIC or route in API_METHODS:
            self._allow = API_METHODS.get(route, "GET, HEAD")
            raise ApiError("METHOD_NOT_ALLOWED")
        raise ApiError("NOT_FOUND")

    def do_PUT(self):
        self._handle(self._other, head_only=False)

    do_DELETE = do_PATCH = do_OPTIONS = do_PUT

    # ── 공통 처리: 예외 → 응답, 요청마다 로그 한 줄 ──
    def _handle(self, fn, *, head_only: bool) -> None:
        self._begin()
        self._allow = ""
        try:
            fn(head_only=head_only)
        except ApiError as err:
            extra = {"Allow": self._allow} if err.code == "METHOD_NOT_ALLOWED" and self._allow else None
            try:
                self._error(err, head_only=head_only, extra=extra)
                if self._unread_body:
                    self._linger()
            except OSError:
                self.close_connection = True
        except RequesterLeft:
            self.close_connection = True
            self._code = "CLIENT_GONE"
        except ClientGone:
            # 본문이 Content-Length보다 덜 왔다(업로드 도중 창 닫기·취소, 또는 잘린 요청).
            # 400을 한 번 시도하고, 클라이언트가 이미 없으면 조용히 끝낸다(트레이스백 없음).
            self.close_connection = True
            try:
                self._error(ApiError("BAD_REQUEST", message="본문이 Content-Length보다 짧습니다."))
            except OSError:
                self._code = "CLIENT_GONE"
        except OSError:
            # 응답을 쓰는 중에 끊김(사용자가 기다리다 새로고침 등) — 서버 문제가 아니다.
            self.close_connection = True
            self._code = self._code or "CLIENT_GONE"
        except Exception as exc:  # noqa: BLE001 — 예상 못 한 예외: 로그엔 타입만, 클라이언트엔 일반 문구
            self.close_connection = True
            try:
                self._error(ApiError("INTERNAL", log=f"{type(exc).__name__} at line {_tb_line(exc)}"))
            except OSError:
                pass
        finally:
            self._finish_log()


def _tb_line(exc: BaseException) -> str:
    """예외가 난 이 파일 안의 마지막 줄 번호(코드 위치만, 내용 없음)."""
    tb = exc.__traceback__
    line = "?"
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == __file__:
            line = str(tb.tb_lineno)
        tb = tb.tb_next
    return line


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        # 기본 구현은 트레이스백 전체를 redact 없이 stderr에 찍는다. 타입만 남긴다.
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return  # 클라이언트가 먼저 끊었거나 놀다가 시간 초과 — 흔하고 무해하다
        log(f"요청 처리 중 예외 {type(exc).__name__}")


def startup_report() -> None:
    try:
        _key, source, warnings = config.find_api_key()
        log(f"API 키 확인: {source}")  # 출처만. 키는 찍지 않는다.
        for w in warnings:
            log(f"[경고] {w}")
    except config.MissingKeyError:
        log("[경고] API 키가 없습니다. 추출은 되지만 /api/summarize는 NO_KEY(500)로 응답합니다. .env.example 참고.")
    tools = pdf_extract.check_tools()
    if not all(tools.values()):
        missing = ", ".join(n for n, ok in tools.items() if not ok)
        log(f"[경고] PDF 도구가 없습니다({missing}): sudo apt install poppler-utils — /api/extract는 TOOL_MISSING(500)")
    layout_missing = [n for n in LAYOUT_TOOLS if shutil.which(n) is None]
    if layout_missing:
        log(f"[경고] 보기·내려받기 도구가 없습니다({', '.join(layout_missing)}): sudo apt install poppler-utils "
            "— /api/layout·/api/export는 TOOL_MISSING(500)")
    if pdf_layout is None:
        log(f"[경고] pdf_layout.py를 불러오지 못했습니다({_LAYOUT_IMPORT_ERROR}). /api/layout·/api/export는 INTERNAL(500)")
    if llm is None:
        log(f"[경고] llm.py를 불러오지 못했습니다({_LLM_IMPORT_ERROR}). /api/summarize는 INTERNAL(500)로 응답합니다.")
    missing_static = sorted({n for n, _ in STATIC.values() if not (ROOT / n).is_file()})
    if missing_static:
        log(f"[알림] 아직 없는 정적 파일: {', '.join(missing_static)} (404로 응답)")


def main() -> None:
    startup_report()
    try:
        httpd = Server((HOST, PORT), Handler)
    except OSError as exc:
        log(f"{HOST}:{PORT}에 바인딩하지 못했습니다({type(exc).__name__}). 이미 실행 중인지 확인하세요: ss -ltnp | grep :{PORT}")
        raise SystemExit(1)
    log(f"http://{HOST}:{PORT}/ 에서 실행 중 (끄려면 Ctrl+C)")

    # run.sh는 Ctrl+C·터미널 닫힘을 SIGTERM으로 넘긴다(백그라운드 자식은 SIGINT를 무시한다).
    def _on_term(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _on_term)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        log("종료")


if __name__ == "__main__":
    main()
