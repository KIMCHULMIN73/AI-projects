"""가짜 OpenRouter(/api/v1/chat/completions) — 통합 시험용. 개발 전용(앱은 이 파일을 로드하지 않는다). 모델 호출 0회.

    python3 -B tools/fake_upstream.py [PORT]        # 기본 127.0.0.1:8033

tools/e2e_app.py가 실제 server.py를 띄우면서 llm.API_URL만 이 주소로 바꾼다. 그러면 실제 서버·실제 화면은
그대로 두고 업스트림 응답만 마음대로 고를 수 있다(하루 한도·분당 한도·5xx·느린 응답·깨진 JSON·XSS 문자열).

제어(시험 스크립트가 부른다):
    POST /__ctl/reset                         기록·대본 비우기(기본 응답 = 정상 요약)
    POST /__ctl/script  {"script": [항목…], "default": 항목|null}
         항목: {"ok": true}                     요청 종류(조각/최종)에 맞는 정상 JSON
               {"status": 429, "daily": true, "reset": epoch_ms}   하루 한도 429(free-models-per-day)
               {"status": 429}                 일반 429 / {"status": 500} 등
               {"content": "모델 원문", "finish": "stop"}
               {"summary": {...}}               최종 요약 JSON을 직접 지정(XSS 문자열 등)
               공통 "delay": 초                  응답 전에 기다림
    GET  /__ctl/log                            받은 요청 기록 [{model, kind, chars, auth_fake, t, done}]
"""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FAKE_KEY = "sk-or-v1-" + "f" * 64   # e2e는 진짜 키 대신 이 키로 서버를 띄운다

GOOD_SUMMARY = {
    "one_line": "가짜 업스트림이 만든 시험용 한 줄 요약이다.",
    "overview": "가짜 업스트림의 첫째 문장이다. 둘째 문장이다. 셋째 문장이다.",
    "points": ["첫째 요점이다.", "둘째 요점이다.", "셋째 요점이다."],
}
GOOD_PARTIAL = {"points": ["부분 요점 하나다.", "부분 요점 둘이다."]}

LOCK = threading.Lock()
STATE: dict = {"log": [], "script": [], "default": {"ok": True}}


def ok_body(content: str, finish: str = "stop") -> dict:
    return {"id": "fake", "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10}}


def kind_of(req: dict) -> str:
    msgs = req.get("messages") or [{}]
    system = str(msgs[0].get("content", ""))
    user = str(msgs[-1].get("content", ""))
    if "one_line" not in system:
        return "partial"
    return "merge" if "<partial_summaries>" in user else "summary"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _reply(self, code: int, obj, headers: dict | None = None) -> None:
        data = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            for k, v in (headers or {}).items():
                self.send_header(k, str(v))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path == "/__ctl/log":
            with LOCK:
                return self._reply(200, STATE["log"])
        self._reply(404, {"error": "no"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if self.path == "/__ctl/reset":
            with LOCK:
                STATE.update(log=[], script=[], default={"ok": True})
            return self._reply(200, {"ok": True})
        if self.path == "/__ctl/script":
            body = json.loads(raw or b"{}")
            with LOCK:
                STATE["script"] = list(body.get("script") or [])
                STATE["default"] = body.get("default") or {"ok": True}
            return self._reply(200, {"ok": True})
        if self.path != "/api/v1/chat/completions":
            return self._reply(404, {"error": "no"})
        req = json.loads(raw)
        kind = kind_of(req)
        entry = {"model": req.get("model"), "kind": kind, "chars": len(str(req["messages"][-1]["content"])),
                 "auth_fake": self.headers.get("Authorization", "") == f"Bearer {FAKE_KEY}",
                 "t": time.time(), "done": False}
        with LOCK:
            STATE["log"].append(entry)
            item = STATE["script"].pop(0) if STATE["script"] else STATE["default"]
        if item.get("delay"):
            time.sleep(float(item["delay"]))
        entry["done"] = True
        status = int(item.get("status", 200))
        if status != 200:
            headers = {}
            if item.get("daily"):
                msg = "Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests per day"
                if item.get("reset"):
                    headers = {"X-RateLimit-Limit": "50", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": item["reset"]}
            else:
                msg = f"fake error {status}"
            return self._reply(status, {"error": {"code": status, "message": msg}}, headers)
        if "content" in item:
            return self._reply(200, ok_body(item["content"], item.get("finish", "stop")))
        if "summary" in item and kind != "partial":
            return self._reply(200, ok_body(json.dumps(item["summary"], ensure_ascii=False)))
        body = GOOD_PARTIAL if kind == "partial" else GOOD_SUMMARY
        return self._reply(200, ok_body(json.dumps(body, ensure_ascii=False)))


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8033
    httpd = Server(("127.0.0.1", port), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
