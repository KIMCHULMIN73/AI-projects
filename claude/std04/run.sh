#!/usr/bin/env bash
# 냉장고 레시피 앱 실행기 — 로컬 프록시(server.py)를 켜고 브라우저를 연다.
#
#   ./run.sh            → 8010번으로 실행 (이 앱이 이미 떠 있으면 브라우저만 연다)
#   ./run.sh 8080       → 다른 포트로
#   NO_OPEN=1 ./run.sh  → 브라우저 자동 실행 없이
#
# 포트를 고정하는 이유: 프로필·보관함은 브라우저 localStorage에 저장되는데, localStorage는
# 주소의 포트마다 따로 보관된다. server.py에 맡겨 빈 포트를 고르게 하면 그날 상황에 따라
# 번호가 바뀌어 보관함이 사라진 것처럼 보인다. 다른 포트를 쓰려면 매번 같은 번호로 줄 것.
#
# index.html을 더블클릭(file://)하면 안 된다 — 모듈 스크립트가 막히고, 키를 쥔 프록시가 없다.

set -euo pipefail
cd "$(dirname "$0")"

PORT="${1:-8010}"

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1024 || PORT > 65535 )); then
  echo "포트는 1024~65535 사이의 숫자여야 합니다: ${PORT}" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3이 없습니다." >&2
  exit 1
fi

URL="http://127.0.0.1:${PORT}/"

open_browser() {
  # server.py와 같은 조건: 그래픽 환경이 있고 NO_OPEN이 아닐 때만
  if [ -z "${NO_OPEN:-}" ] && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 || true
  fi
}

# 포트 상태를 free / ours(이 앱이 이미 떠 있음) / other 중 하나로 알려 준다.
# 이 앱인지는 응답의 Server 헤더(server.py의 server_version = "fridge-proxy")로 가린다.
port_state() {
  python3 - "$PORT" <<'PY'
import socket, sys, urllib.error, urllib.request
port = int(sys.argv[1])
with socket.socket() as s:
    try:
        s.bind(("127.0.0.1", port))
        print("free")
        sys.exit(0)
    except OSError:
        pass
try:
    server = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2).headers.get("Server", "")
except urllib.error.HTTPError as e:          # NO_KEY 등 503이어도 이 앱이다
    server = e.headers.get("Server", "")
except Exception:
    server = ""
print("ours" if server.startswith("fridge-proxy") else "other")
PY
}

case "$(port_state)" in
  ours)
    echo "냉장고 레시피 앱이 이미 ${URL} 에서 실행 중입니다. 브라우저만 엽니다."
    echo "  (끄려면 그 서버를 띄운 터미널에서 Ctrl+C)"
    open_browser
    exit 0
    ;;
  other)
    echo "포트 ${PORT}번은 다른 프로그램이 쓰고 있습니다." >&2
    echo "  사용 중인 프로세스 확인:  ss -ltnp | grep :${PORT}" >&2
    echo "  다른 포트로 실행:         ./run.sh $((PORT + 1))" >&2
    echo "  (보관함은 포트마다 따로라서, 다음에도 같은 번호로 실행해야 이어집니다)" >&2
    exit 1
    ;;
esac

if [ ! -f Models ]; then
  echo "※ Models 파일이 없습니다. 화면에 안내가 뜨고 분석·추천은 동작하지 않습니다(CLAUDE.md의 모델 표 참고)." >&2
fi

# 키 확인·브라우저 열기·127.0.0.1 바인딩은 server.py가 한다.
exec python3 server.py "$PORT"
