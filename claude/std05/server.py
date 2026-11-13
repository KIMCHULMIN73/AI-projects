"""AI 공감 다이어리 서버 — 정적 파일 3개 + POST /api/analyze.

    python3 server.py          # http://127.0.0.1:8020/  (보통은 ./run.sh)

- 127.0.0.1:8020 고정. 외부에 열지 않는다.
- 정적 파일은 허용 목록(STATIC)만. 그 밖의 경로(config.py, .env, tools/ …)는 전부 404.
- Host 헤더가 127.0.0.1:8020 / localhost:8020이 아니면 403(DNS 리바인딩 방지).
- API 키는 llm.py → config.py 안에서만 쓰인다. 응답·로그로 나가지 않는다(로그는 config.redact 통과).
"""

from __future__ import annotations

import json
import signal
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import config
import llm

HOST = "127.0.0.1"
PORT = 8020
ROOT = Path(__file__).resolve().parent
SERVER_NAME = "empathy-diary"  # run.sh가 "이 앱이 이미 떠 있는지"를 Server 헤더로 가린다

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
MAX_BODY = 16 * 1024
BODY_READ_TIMEOUT_S = 10

# 오류 코드 → (HTTP 상태, 사용자에게 보여줄 한국어 설명)
ERRORS = {
    "BAD_REQUEST": (400, "요청 형식이 올바르지 않습니다."),
    "TOO_LONG": (400, f"일기는 {llm.TEXT_MAX}자 이내로 써 주세요."),
    "NO_KEY": (500, "서버에 API 키가 설정되어 있지 않습니다. 관리자에게 문의해 주세요."),
    "RATE_LIMIT": (429, "지금 AI 사용량이 많습니다. 잠시 후 다시 시도해 주세요."),
    "TIMEOUT": (504, "AI 응답이 너무 오래 걸립니다. 잠시 후 다시 시도해 주세요."),
    "UPSTREAM": (502, "AI 서비스에 문제가 생겼습니다. 잠시 후 다시 시도해 주세요."),
    "BAD_OUTPUT": (502, "AI 응답을 이해하지 못했습니다. 다시 시도해 주세요."),
    # 계약 표 밖 — 경로·메서드·Host 오류(프런트가 정상 사용 중에는 만나지 않는다)
    "NOT_FOUND": (404, "찾을 수 없는 주소입니다."),
    "METHOD_NOT_ALLOWED": (405, "허용되지 않는 요청 방식입니다."),
    "FORBIDDEN_HOST": (403, "허용되지 않은 주소로 접속했습니다. http://127.0.0.1:8020/ 으로 접속해 주세요."),
    "INTERNAL": (500, "서버 내부 오류가 발생했습니다."),
}


class ApiError(Exception):
    def __init__(self, code: str, status: int | None = None, message: str | None = None):
        super().__init__(code)
        self.code = code
        default_status, default_msg = ERRORS[code]
        self.status = status or default_status
        self.message = message or default_msg


def log(line: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    print(f"[{stamp}] {config.redact(line)}", file=sys.stderr, flush=True)


def validate_text(body: object) -> str:
    """요청 JSON → 정규화된 text. 계약: 앞뒤 공백 제거 후 1~200자, 줄바꿈은 공백으로."""
    if not isinstance(body, dict):
        raise ApiError("BAD_REQUEST", message="요청 본문은 JSON 객체여야 합니다.")
    text = body.get("text")
    if not isinstance(text, str):
        raise ApiError("BAD_REQUEST", message="text 필드(문자열)가 필요합니다.")
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in text):
        # JSON의 짝 없는 \ud800 같은 값. 글자가 아니므로 모델로 보내지 않는다.
        raise ApiError("BAD_REQUEST", message="글자로 읽을 수 없는 문자가 섞여 있습니다.")
    text = llm.normalize_text(text)
    if not text:
        raise ApiError("BAD_REQUEST", message="오늘 있었던 일을 한 줄 써 주세요.")
    if len(text) > llm.TEXT_MAX:
        raise ApiError("TOO_LONG")
    return text


class Handler(BaseHTTPRequestHandler):
    server_version = SERVER_NAME
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 60  # 느린 클라이언트가 스레드를 무한정 붙잡지 않게

    # 기본 로그는 요청 줄(쿼리 포함)을 그대로 찍는다. 경로만 남기고, 전부 redact를 거친다.
    def version_string(self):  # 파이썬 버전을 알리지 않는다
        return SERVER_NAME

    def log_message(self, fmt, *args):
        log(str(fmt % args)[:200])

    def log_request(self, code="-", size="-"):
        log(f"{self.command} {self.path.split('?', 1)[0][:200]} → {code}")

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
        self._json(err.status, {"error": {"code": err.code, "message": err.message}}, **kw)

    def _host_ok(self) -> bool:
        return (self.headers.get("Host") or "").strip().lower() in ALLOWED_HOSTS

    # ── GET / HEAD ──
    def do_GET(self):
        self._get(head_only=False)

    def do_HEAD(self):
        self._get(head_only=True)

    def _get(self, *, head_only: bool) -> None:
        if not self._host_ok():
            return self._error(ApiError("FORBIDDEN_HOST"), head_only=head_only)
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path == "/api/analyze":
            return self._error(ApiError("METHOD_NOT_ALLOWED"), head_only=head_only, extra={"Allow": "POST"})
        entry = STATIC.get(path)
        if entry is None:
            return self._error(ApiError("NOT_FOUND"), head_only=head_only)
        name, ctype = entry
        try:
            data = (ROOT / name).read_bytes()  # 매번 읽는다: 작고, 프런트가 고치는 즉시 반영된다
        except OSError:
            return self._error(ApiError("NOT_FOUND"), head_only=head_only)
        self._send(200, data, ctype, head_only=head_only, extra={"Cache-Control": "no-cache"})

    # ── POST ──
    def do_POST(self):
        if not self._host_ok():
            self.close_connection = True
            return self._error(ApiError("FORBIDDEN_HOST"))
        path = self.path.split("?", 1)[0]
        if path != "/api/analyze":
            self.close_connection = True
            return self._error(ApiError("NOT_FOUND"))
        try:
            text = validate_text(self._read_json_body())
            started = time.monotonic()
            try:
                result = llm.analyze(text, log=log)
            except llm.AnalyzeError as exc:
                log(f"analyze 실패 {exc.code}: {exc.log}")
                raise ApiError(exc.code)
            log(f"analyze 성공 {time.monotonic() - started:.1f}s 입력 {len(text)}자 "
                f"→ {result['emotion']}/{result['intensity']} crisis={result['crisis']}")
            self._json(200, result)
        except ApiError as err:
            self._error(err)
        except ConnectionError:
            # 사용자가 기다리다 새로고침·창 닫기·취소를 하면 응답을 쓰는 중에 연결이 끊긴다.
            # 서버 문제가 아니므로 오류 응답을 다시 쓰려 하지 않고(또 끊겨 트레이스백이 난다) 조용히 끝낸다.
            self.close_connection = True
            log("클라이언트가 응답 전에 연결을 끊음")
        except Exception as exc:  # 예상 못 한 예외: 내용은 로그에도 타입만, 클라이언트엔 일반 문구
            log(f"내부 오류 {type(exc).__name__}")
            self.close_connection = True
            try:
                self._error(ApiError("INTERNAL"))
            except OSError:
                pass

    def _read_json_body(self) -> object:
        # 오류로 끝나는 경우 본문을 다 읽지 않았을 수 있으므로 연결을 닫는다.
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            self.close_connection = True
            raise ApiError("BAD_REQUEST", status=415, message="Content-Type은 application/json이어야 합니다.")
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            raise ApiError("BAD_REQUEST", message="Content-Length가 있는 본문만 받습니다.")
        raw_len = self.headers.get("Content-Length")
        try:
            length = int(raw_len) if raw_len is not None else -1
        except ValueError:
            length = -1
        if length < 0:
            self.close_connection = True
            raise ApiError("BAD_REQUEST", message="Content-Length가 필요합니다.")
        if length > MAX_BODY:
            self.close_connection = True
            raise ApiError("BAD_REQUEST", status=413, message="요청 본문이 너무 큽니다(최대 16KB).")
        prev = self.connection.gettimeout()
        self.connection.settimeout(BODY_READ_TIMEOUT_S)
        try:
            raw = self.rfile.read(length)
        except OSError:
            self.close_connection = True
            raise ApiError("BAD_REQUEST", message="요청 본문을 읽지 못했습니다.")
        finally:
            self.connection.settimeout(prev)
        if len(raw) != length:
            self.close_connection = True
            raise ApiError("BAD_REQUEST", message="요청 본문이 잘렸습니다.")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ApiError("BAD_REQUEST", message="요청 본문이 올바른 JSON이 아닙니다.")

    # 지원하지 않는 메서드도 JSON 오류로
    def _not_allowed(self):
        self.close_connection = True
        if not self._host_ok():
            return self._error(ApiError("FORBIDDEN_HOST"))
        self._error(ApiError("METHOD_NOT_ALLOWED"))

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _not_allowed


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        # 기본 구현은 트레이스백 전체를 redact 없이 stderr에 찍는다. 타입만 남긴다.
        exc = sys.exc_info()[1]
        if isinstance(exc, ConnectionError):
            return  # 클라이언트가 먼저 끊은 것 — 흔하고 무해하다
        log(f"요청 처리 중 예외 {type(exc).__name__}")


def main() -> None:
    try:
        _key, source, warnings = config.find_api_key()
        log(f"API 키 확인: {source}")  # 출처만. 키는 찍지 않는다.
        for w in warnings:
            log(f"[경고] {w}")
    except config.MissingKeyError:
        log("[경고] API 키가 없습니다. /api/analyze는 NO_KEY(500)로 응답합니다. .env.example 참고.")
    log(f"모델: 1순위 {llm.PRIMARY_MODEL} / 폴백 {llm.FALLBACK_MODEL}")
    missing = sorted({n for n, _ in STATIC.values() if not (ROOT / n).is_file()})
    if missing:
        log(f"[알림] 아직 없는 정적 파일: {', '.join(missing)} (404로 응답)")
    try:
        httpd = Server((HOST, PORT), Handler)
    except OSError as exc:
        log(f"{HOST}:{PORT}에 바인딩하지 못했습니다({type(exc).__name__}). 이미 실행 중인지 확인하세요: ss -ltnp | grep :{PORT}")
        raise SystemExit(1)
    log(f"http://{HOST}:{PORT}/ 에서 실행 중 (끄려면 Ctrl+C)")

    # run.sh는 Ctrl+C·터미널 닫힘을 SIGTERM으로 넘긴다(백그라운드 자식은 SIGINT를 무시한다).
    # Ctrl+C와 같은 길로 끝나게 해서 소켓을 닫고 '종료'를 남긴다.
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
