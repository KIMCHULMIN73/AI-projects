#!/usr/bin/env bash
# std03 루트에서 바로 게임을 띄우기 위한 래퍼.
#
# 실제 서버 실행 로직은 quiz-game/run.sh 한 곳에만 있다. 여기서 그것을 그대로
# 호출할 뿐이므로 포트 탐색·브라우저 자동 실행·NO_OPEN 같은 동작은 전부 동일하다.
# 로직을 복사해 두 벌로 만들면 반드시 갈라지므로 그렇게 하지 않는다.
#
#   ./run.sh          → 8000번부터 비어 있는 포트를 찾아 실행
#   ./run.sh 8080     → 8080번 고정
#   NO_OPEN=1 ./run.sh → 브라우저 자동 실행 없이 서버만

set -euo pipefail

REAL_RUN="$(dirname "$0")/quiz-game/run.sh"

if [ ! -x "$REAL_RUN" ]; then
  echo "quiz-game/run.sh 를 찾을 수 없거나 실행 권한이 없습니다: ${REAL_RUN}" >&2
  echo "  확인:  ls -l ${REAL_RUN}" >&2
  exit 1
fi

exec "$REAL_RUN" "$@"
