#!/usr/bin/env python3
"""프런트엔드 확인용 가짜 서버. 개발 전용 — 앱은 이 파일을 로드하지 않는다.

실제 서버(server.py, 포트 8020)와 겹치지 않게 기본 포트는 8021이다.
    python3 tools/mock_server.py [포트]

정적 파일은 index.html · style.css · app.js 세 개만 내보낸다(실제 서버의 허용 목록과 같다).
POST /api/analyze 는 입력 글에 든 낱말로 응답을 고른다(여러 개를 섞어 써도 된다):

  감정 이름(기쁨·설렘·평온·슬픔·불안·분노·지침·외로움)  그 감정으로 성공. 없으면 평온
  세기N (N=1~5)                                          intensity를 N으로
  위기                                                   crisis: true
  오류:CODE  (BAD_REQUEST·TOO_LONG·NO_KEY·RATE_LIMIT·TIMEOUT·UPSTREAM·BAD_OUTPUT)  그 오류로 실패
  느림 / 느림:N                                          5초(또는 N초) 늦게 응답
  비JSON                                                 502 + HTML 본문
  빈성공                                                 200 + {} (모양이 틀린 성공)
  이상값                                                 emotion "놀람", intensity 9 (계약 밖 값)
  긴답                                                   띄어쓰기 없는 아주 긴 메시지

진짜 서버처럼 앞뒤 공백을 지운 뒤 1~200자가 아니면 BAD_REQUEST / TOO_LONG을 준다.
"""

from __future__ import annotations

import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = {"/": "index.html", "/index.html": "index.html", "/style.css": "style.css", "/app.js": "app.js"}
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
EMOTIONS = ["기쁨", "설렘", "평온", "슬픔", "불안", "분노", "지침", "외로움"]
ERRORS = {"BAD_REQUEST": 400, "TOO_LONG": 400, "NO_KEY": 500, "RATE_LIMIT": 429,
          "TIMEOUT": 504, "UPSTREAM": 502, "BAD_OUTPUT": 502}
MESSAGES = {
    "기쁨": "그 순간이 얼마나 반가웠을지 느껴져요. 오늘의 웃음을 오래 기억해 두셨으면 해요.",
    "설렘": "두근거리는 마음이 글에서도 전해져요. 그 기대가 좋은 일로 이어지길 바라요.",
    "평온": "잔잔한 하루를 보내셨군요. 이런 고요함도 소중한 쉼이 되어 줄 거예요.",
    "슬픔": "마음이 많이 무거우셨겠어요. 슬퍼하는 것도 괜찮아요. 오늘은 스스로를 조금 더 다독여 주세요.",
    "불안": "앞일이 걱정되는 마음, 충분히 그럴 수 있어요. 지금 할 수 있는 작은 것 하나부터 해 봐요.",
    "분노": "속상하고 화가 나셨을 만해요. 그 감정을 알아차린 것만으로도 잘하고 계신 거예요.",
    "지침": "오늘 정말 애쓰셨어요. 쉬어 가도 괜찮아요. 따뜻한 물 한 잔과 함께 몸을 좀 풀어 주세요.",
    "외로움": "혼자인 것 같은 마음이 드셨군요. 이렇게 털어놓아 주셔서 고마워요. 당신의 이야기를 듣고 있어요.",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "MockDiary/1"

    def log_message(self, fmt, *args):  # 조용히
        if "-v" in sys.argv:
            super().log_message(fmt, *args)

    def send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status: int, obj) -> None:
        self.send(status, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def error(self, code: str, status: int | None = None) -> None:
        self.send_json(status or ERRORS[code], {"error": {"code": code, "message": f"mock {code}"}})

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        name = STATIC.get(path)
        if not name:
            return self.send(404, b"not found", "text/plain; charset=utf-8")
        f = ROOT / name
        self.send(200, f.read_bytes(), TYPES[f.suffix])

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/api/analyze":
            return self.send(404, b"not found", "text/plain; charset=utf-8")
        n = int(self.headers.get("Content-Length") or 0)
        if n > 16_384:
            return self.error("BAD_REQUEST", 413)
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            return self.error("BAD_REQUEST", 415)
        try:
            data = json.loads(self.rfile.read(n) or b"null")
            text = data["text"]
            assert isinstance(text, str)
        except Exception:
            return self.error("BAD_REQUEST")
        text = text.strip()
        if not text:
            return self.error("BAD_REQUEST")
        if len(text) > 200:
            return self.error("TOO_LONG")

        m = re.search(r"느림(?::(\d+))?", text)
        if m:
            time.sleep(int(m.group(1) or 5))

        m = re.search(r"오류:([A-Z_]+)", text)
        if m and m.group(1) in ERRORS:
            return self.error(m.group(1))
        if "비JSON" in text:
            return self.send(502, b"<html><body>Bad Gateway</body></html>", "text/html")
        if "빈성공" in text:
            return self.send_json(200, {})
        if "이상값" in text:
            return self.send_json(200, {"emotion": "놀람", "intensity": 9, "message": "생각지 못한 일이 있었군요.", "crisis": "yes"})

        emotion = next((e for e in EMOTIONS if e in text), "평온")
        m = re.search(r"세기([1-5])", text)
        intensity = int(m.group(1)) if m else 3
        message = MESSAGES[emotion]
        if "긴답" in text:
            message = "가" * 400
        message += f"\n(적어 주신 글: {text})"   # 사용자 글이 그대로 화면에 글자로 나오는지 보려고 되돌려 준다
        crisis = "위기" in text
        self.send_json(200, {"emotion": emotion, "intensity": intensity, "message": message, "crisis": crisis})


def main() -> None:
    port = int(next((a for a in sys.argv[1:] if a.isdigit()), "8021"))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"가짜 서버: http://127.0.0.1:{port}/  (Ctrl+C로 종료)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
