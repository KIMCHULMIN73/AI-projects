"""실제 server.py를 그대로 띄우되 llm.API_URL만 가짜 업스트림으로 바꾼다. 개발 전용(앱은 이 파일을 로드하지 않는다).

    OPENROUTER_API_KEY=<가짜 키> python3 -B tools/e2e_app.py http://127.0.0.1:8033/api/v1/chat/completions [호출_제한초 총_예산초]

호출 제한·총 예산(llm.CALL_TIMEOUT_S·TOTAL_BUDGET_S)을 주면 TIMEOUT 경로를 몇 초 안에 시험할 수 있다.
환경변수 E2E_LAYOUT_DELAY(초)를 주면 보기·내려받기 처리를 늦춰 LAYOUT_SLOTS BUSY를 시험할 수 있다.

SPEC §5.2: 엔드포인트는 llm.API_URL 상수 하나이고 환경변수 통로는 두지 않는다 — 그래서 시험 쪽에서 상수를 바꿔 끼운다.
"""

from __future__ import annotations

import os
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

# 환경변수 E2E_LAYOUT_DELAY=초: /api/layout·/api/export 처리(처리 자리를 잡은 뒤)를 그만큼 늦춘다 — LAYOUT_SLOTS BUSY 시험용.
_delay = float(os.environ.get("E2E_LAYOUT_DELAY") or 0)
if _delay > 0 and server.pdf_layout is not None:
    import time

    import pdf_layout  # noqa: E402

    _layout, _export = pdf_layout.layout, pdf_layout.export_html

    def _slow_layout(*a, **k):
        time.sleep(_delay)
        return _layout(*a, **k)

    def _slow_export(*a, **k):
        time.sleep(_delay)
        return _export(*a, **k)

    pdf_layout.layout, pdf_layout.export_html = _slow_layout, _slow_export

if __name__ == "__main__":
    server.main()
