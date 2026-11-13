"""무료 모델 후보를 대표 입력으로 실제 호출해 비교한다(개발 전용, 앱은 이 파일을 쓰지 않는다).

    python3 -B tools/compare_models.py                      # 기본 후보 3개 × 입력 5개 = 15회 호출
    python3 -B tools/compare_models.py MODEL_ID [MODEL_ID…] # 지정한 모델만
    python3 -B tools/compare_models.py --list               # 지금 OpenRouter의 :free 텍스트 모델 목록(호출 아님)

**:free로 끝나지 않는 모델 ID는 거부한다**(이 계정은 크레딧이 있어 유료 호출이 실제로 과금된다).
프롬프트·검증은 llm.py의 것을 그대로 쓴다. 출력: 모델별 응답 시간, JSON 형식 준수
(그대로 json.loads 되는지 / 코드펜스 등 보정이 필요했는지), 계약 변환 결과, 메시지 원문.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import llm  # noqa: E402

DEFAULT_CANDIDATES = (
    "baidu/ernie-5.5-flash-preview:free",
    "nvidia/nemotron-3.5-lightning:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
)
SAMPLES = (
    ("기쁨", "3개월 동안 준비한 자격증 시험에 드디어 합격했다!"),
    ("슬픔", "10년 같이 산 강아지가 오늘 아침 무지개다리를 건넜다."),
    ("분노", "팀 프로젝트에서 내가 다 한 걸 팀장이 자기가 했다고 발표했다."),
    ("애매", "그냥 평소처럼 출근하고 퇴근했다. 별일은 없었는데 좀 멍하다."),
    ("위기", "요즘 너무 힘들어서 그냥 다 끝내고 사라지고 싶다는 생각이 든다."),
)


def list_free() -> None:
    with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=20) as r:
        data = json.load(r)
    for m in data.get("data", []):
        mods = (m.get("architecture") or {}).get("input_modalities") or []
        if m["id"].endswith(":free") and "text" in mods:
            print(m["id"])


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--list":
        list_free()
        return 0
    models = argv or list(DEFAULT_CANDIDATES)
    for m in models:
        llm.assert_free(m)
    calls = 0
    summary = []
    for model in models:
        print(f"\n===== {model}")
        lat, strict_ok, ok = [], 0, 0
        for label, text in SAMPLES:
            t0 = time.monotonic()
            calls += 1
            try:
                data = llm.call_with_deadline(model, llm.build_messages(text), llm.PER_CALL_TIMEOUT_S)
            except llm.CallFailure as exc:
                dt = time.monotonic() - t0
                print(f"  [{label}] 실패 {exc.kind} {dt:.1f}s")
                continue
            dt = time.monotonic() - t0
            lat.append(dt)
            choice = (data.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content") or ""
            try:
                json.loads(content)
                strict = True
            except ValueError:
                strict = False
            strict_ok += strict
            try:
                result = llm.parse_choice(data, model, text)
                ok += 1
                print(f"  [{label}] {dt:.1f}s strictJSON={strict} finish={choice.get('finish_reason')} "
                      f"→ {result['emotion']}/{result['intensity']} crisis={result['crisis']} "
                      f"({len(result['message'])}자)")
                print(f"      {result['message']}")
            except llm.CallFailure as exc:
                print(f"  [{label}] {dt:.1f}s 검증 실패 {exc.log} / 원문 앞부분: {content[:120]!r}")
        avg = sum(lat) / len(lat) if lat else float("nan")
        summary.append((model, ok, strict_ok, avg, max(lat) if lat else float("nan")))
    print("\n===== 요약 (계약 통과 / 그대로 JSON / 평균·최대 응답시간)")
    for model, ok, strict_ok, avg, mx in summary:
        print(f"  {model:45s} {ok}/{len(SAMPLES)}  {strict_ok}/{len(SAMPLES)}  {avg:.1f}s / {mx:.1f}s")
    print(f"\n실제 모델 호출 수: {calls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
