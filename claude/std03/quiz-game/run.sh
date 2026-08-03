#!/usr/bin/env bash
# 로컬 정적 서버 실행 래퍼.
#
# index.html을 파일로 직접 열면(file://) 브라우저가 ES Modules import를 CORS 정책으로
# 막기 때문에 게임이 뜨지 않는다. 반드시 이 스크립트(또는 동등한 정적 서버)로 띄울 것.
# 빌드 도구가 아니라 그냥 파일 서빙이다.
#
#   ./run.sh          → 8000번부터 비어 있는 포트를 찾아 실행
#   ./run.sh 8080     → 8080번 고정(이미 쓰는 중이면 그냥 알려 주고 멈춘다)
#
# 외부에 노출되지 않도록 127.0.0.1에만 바인딩한다.

set -euo pipefail
cd "$(dirname "$0")"

DEFAULT_PORT=8000
REQUESTED="${1:-}"
HOST=127.0.0.1

# 포트가 비어 있는지 확인한다(0=비어 있음).
port_is_free() {
  python3 - "$1" <<'PY'
import socket, sys
with socket.socket() as s:
    try:
        s.bind(("127.0.0.1", int(sys.argv[1])))
    except OSError:
        sys.exit(1)
sys.exit(0)
PY
}

if [ -n "$REQUESTED" ]; then
  # 포트를 명시했으면 마음대로 옮기지 않는다 — 이유를 알려 주고 멈춘다.
  PORT="$REQUESTED"
  if ! port_is_free "$PORT"; then
    echo "포트 ${PORT}번은 이미 다른 프로그램이 쓰고 있습니다." >&2
    echo "  사용 중인 프로세스 확인:  ss -ltnp | grep :${PORT}" >&2
    echo "  또는 다른 포트로 실행:    ./run.sh $((PORT + 1))" >&2
    exit 1
  fi
else
  # 기본 실행이면 비어 있는 포트를 알아서 찾는다.
  PORT=""
  for candidate in $(seq "$DEFAULT_PORT" $((DEFAULT_PORT + 20))); do
    if port_is_free "$candidate"; then
      PORT="$candidate"
      break
    fi
  done
  if [ -z "$PORT" ]; then
    echo "${DEFAULT_PORT}~$((DEFAULT_PORT + 20))번 포트가 모두 사용 중입니다." >&2
    echo "  ./run.sh <포트번호> 로 직접 지정해 주세요." >&2
    exit 1
  fi
  if [ "$PORT" != "$DEFAULT_PORT" ]; then
    echo "※ ${DEFAULT_PORT}번 포트가 사용 중이라 ${PORT}번으로 실행합니다."
    echo
  fi
fi

URL="http://localhost:${PORT}/"

echo "누가누가 잘하나 좌충우돌 퀴즈게임"
echo "  게임             → ${URL}"
echo "  미리보기(개발용) → ${URL}preview.html"
echo "  (Ctrl+C 로 종료)"
echo

# 브라우저를 자동으로 열어 준다.
#   - 그래픽 환경이 없으면(SSH 등) 건너뛴다.
#   - 자동으로 열고 싶지 않으면 NO_OPEN=1 ./run.sh
# 서버가 뜬 뒤에 열리도록 잠깐 기다렸다가 백그라운드로 실행한다.
if [ -z "${NO_OPEN:-}" ] && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v xdg-open >/dev/null 2>&1; then
  ( sleep 1; xdg-open "$URL" >/dev/null 2>&1 || true ) &
  echo "브라우저를 여는 중입니다... (안 열리면 위 주소를 직접 입력하세요)"
  echo
fi

exec python3 -m http.server "${PORT}" --bind "${HOST}"
