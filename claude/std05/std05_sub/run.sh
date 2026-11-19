#!/usr/bin/env bash
# 한눈 PDF 요약 실행기 — server.py를 127.0.0.1:8030에 띄우고 브라우저를 연다.
#
#   ./run.sh            → 서버 실행 + 브라우저 열기 (이미 떠 있으면 브라우저만)
#   NO_OPEN=1 ./run.sh  → 브라우저 없이 서버만
#
# 포트는 8030 고정(server.py의 PORT와 같아야 한다). Host 헤더 검사도 이 번호로 한다.
# 공감 다이어리(8020)와 동시에 띄울 수 있다.
# index.html을 file://로 열면 안 된다 — 키를 쥔 서버를 거쳐야 요약이 된다.
# PDF 도구(pdftotext·pdfinfo)나 API 키가 없으면 server.py가 시작할 때 경고를 찍고, 그래도 뜬다.

set -euo pipefail
cd "$(dirname "$0")"

PORT=8030
URL="http://127.0.0.1:${PORT}/"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3이 없습니다." >&2
  exit 1
fi

open_browser() {
  [ -n "${NO_OPEN:-}" ] && return 0
  if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    echo "그래픽 환경이 없어 브라우저를 열지 않습니다. 직접 여세요: ${URL}"
    return 0
  fi
  if command -v xdg-open >/dev/null 2>&1; then
    (xdg-open "$URL" >/dev/null 2>&1 &) || true
  elif command -v chromium >/dev/null 2>&1; then
    (chromium "$URL" >/dev/null 2>&1 &) || true
  else
    echo "브라우저를 찾지 못했습니다. 직접 여세요: ${URL}"
  fi
}

# free(비어 있음) / ours(이 앱이 이미 실행 중) / other(다른 프로그램) 중 하나.
# 이 앱인지는 Server 헤더(server.py의 SERVER_NAME = "pdf-summary/1")가 pdf-summary로 시작하는지로 가린다.
port_state() {
  python3 -I - "$PORT" <<'PY'
import signal, socket, sys, urllib.error, urllib.request
signal.signal(signal.SIGINT, signal.SIG_DFL)  # 시작 중 Ctrl+C에 트레이스백 대신 조용히 끝난다
port = int(sys.argv[1])
with socket.socket() as s:
    # server.py도 SO_REUSEADDR로 바인딩한다. 이게 없으면 방금 끈 서버의 TIME_WAIT 연결 때문에
    # 빈 포트를 "다른 프로그램"으로 오판한다.
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", port))
        print("free"); sys.exit(0)
    except OSError:
        pass
try:
    server = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2).headers.get("Server", "")
except urllib.error.HTTPError as e:
    server = e.headers.get("Server", "")
except Exception:
    server = ""
print("ours" if server.startswith("pdf-summary") else "other")
PY
}

case "$(port_state)" in
  ours)
    echo "한눈 PDF 요약이 이미 ${URL} 에서 실행 중입니다. 브라우저만 엽니다."
    open_browser
    exit 0
    ;;
  other)
    echo "포트 ${PORT}번을 다른 프로그램이 쓰고 있습니다. 확인: ss -ltnp | grep :${PORT}" >&2
    exit 1
    ;;
esac

if ! command -v pdftotext >/dev/null 2>&1 || ! command -v pdfinfo >/dev/null 2>&1; then
  echo "[경고] PDF 도구가 없습니다: sudo apt install poppler-utils (서버는 띄우지만 PDF를 읽지 못합니다)" >&2
fi

python3 -B server.py &
SERVER_PID=$!
# Ctrl+C·kill·터미널 닫힘 어느 경우든 서버를 함께 끈다(서버는 SIGTERM을 받으면 정리하고 끝난다).
# 서버가 실제로 끝날 때까지 기다린 뒤 나간다 — 바로 다시 ./run.sh 하면 끝나 가던 서버를 "이미 실행 중"으로 보던 틈을 막는다(4단계 QA).
cleanup() {
  trap - INT TERM HUP EXIT
  kill "$SERVER_PID" 2>/dev/null || true
  wait "$SERVER_PID" 2>/dev/null || true
}
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
trap 'cleanup; exit 129' HUP
trap cleanup EXIT

# 서버가 응답할 때까지 최대 5초 기다린 뒤 브라우저를 연다.
for _ in $(seq 1 50); do
  if [ "$(port_state)" = "ours" ]; then
    open_browser
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    break
  fi
  sleep 0.1
done

wait "$SERVER_PID"
