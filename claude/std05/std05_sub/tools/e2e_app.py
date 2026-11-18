"""실제 server.py를 그대로 띄우되 llm.API_URL만 가짜 업스트림으로 바꾼다. 개발 전용(앱은 이 파일을 로드하지 않는다).

    OPENROUTER_API_KEY=<가짜 키> python3 -B tools/e2e_app.py http://127.0.0.1:8033/api/v1/chat/completions [호출_제한초 총_예산초]

호출 제한·총 예산(llm.CALL_TIMEOUT_S·TOTAL_BUDGET_S)을 주면 TIMEOUT 경로를 몇 초 안에 시험할 수 있다.

SPEC §5.2: 엔드포인트는 llm.API_URL 상수 하나이고 환경변수 통로는 두지 않는다 — 그래서 시험 쪽에서 상수를 바꿔 끼운다.
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent   # std05_sub (상위 std05의 같은 이름 모듈을 피한다)
sys.path.insert(0, str(APP_DIR))

import llm  # noqa: E402

assert Path(llm.__file__).resolve().parent == APP_DIR, llm.__file__
llm.API_URL = sys.argv[1]
if len(sys.argv) > 3:
    llm.CALL_TIMEOUT_S = float(sys.argv[2])
    llm.TOTAL_BUDGET_S = float(sys.argv[3])
    llm.MIN_CALL_S = min(llm.MIN_CALL_S, llm.CALL_TIMEOUT_S / 2)

import server  # noqa: E402  (server는 sys.modules의 같은 llm을 쓴다)

assert Path(server.__file__).resolve().parent == APP_DIR, server.__file__

if __name__ == "__main__":
    server.main()
