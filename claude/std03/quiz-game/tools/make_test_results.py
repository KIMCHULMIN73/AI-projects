#!/usr/bin/env python3
"""선생님 모드 파이프라인 시험용 **가짜** 결과 파일 생성기 — 개발 전용.

이 환경에는 브라우저도 JS 런타임도 없어서 실제로 게임을 쳐서 결과 파일을
만들 수 없다. 그래서 반출 스키마(quiz-export/v2)를 그대로 흉내 낸 파일을
파이썬으로 만들어 2~5단계를 시험한다.

**만들어 내는 것은 전부 가짜다.** 파일명이 TEST-로 시작하고 payload에
testData: true가 들어간다. 실제 학생 결과와 섞이지 않게 할 것.

일부러 다음 골칫거리를 전부 심는다 — 수집기(results_collect.py)가
이것들을 제대로 다루는지가 2단계 검증의 핵심이다:

  · 같은 이름으로 여러 판  → 김철민_1..3 라벨
  · 한 판이 두 파일에 중복  → name+playedAt으로 걸러야 함
  · 한 판만 친 이름         → 접미사 없이 그대로
  · 앞뒤 공백이 다른 이름   → 정규화 후 같은 이름으로 취급
  · v1 기록(responses 없음) → null로 보존, [] 로 뭉개면 안 됨
  · accuracy가 백분율(70)   → 100으로 나눠 고치고 경고
  · total 0 / correct>total → 레코드만 버림
  · 깨진 JSON / 다른 앱     → 파일만 건너뜀

    python3 tools/make_test_results.py
"""

import json
import argparse
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_bank as vb  # 문항 파싱 + 샘플링 이식본을 그대로 재사용한다

ROOT = Path(__file__).resolve().parent.parent
INBOX = ROOT / "data" / "results" / "inbox"

POINTS = {"top": 50, "high": 40, "mid": 30, "low": 20, "bottom": 10}
# 실력(0~1)이 같아도 어려운 문제일수록 덜 맞는다. 난이도별 기본 정답률.
BASE_P = {"bottom": 0.92, "low": 0.80, "mid": 0.62, "high": 0.42, "top": 0.25}

# 일부러 망가뜨린 문항: {문항id: 사람들이 몰려가는 오답 index}.
# "정답이 둘인 문항"을 흉내 낸다 — 아는 사람일수록 다른 보기를 고르는 상황이라
# 4단계의 answer-suspect(🔴) 플래그가 실제로 걸리는지 시험하기 위한 것이다.
# (CLAUDE.md 사후 수정 §5의 SC-012·KH-009가 실제로 이 모양이었다.)
TRAPS = {}


def load_bank():
    bank = []
    for category, filename in vb.CATEGORY_FILES.items():
        source = (vb.BANK_DIR / filename).read_text(encoding="utf-8")
        bank.extend(vb.parse_bank(source, category))
    return bank


def play(bank, name, skill, when, exclude):
    """한 판을 흉내 낸다. skill이 높을수록 잘 맞힌다."""
    questions = vb.get_quiz_set(bank, exclude)
    score, correct, responses = 100, 0, []
    tally = {c: [0, 0] for c in vb.CATEGORY_ORDER}
    for q in questions:
        p = min(0.98, max(0.05, BASE_P[q["difficulty"]] + (skill - 0.5) * 0.55))
        hit = random.random() < p
        trap = TRAPS.get(q["id"])
        if trap is not None and trap != q["answerIndex"] and random.random() < 0.72:
            hit = False           # 다들 같은 오답으로 몰려간다
        if hit:
            score += POINTS[q["difficulty"]]
            correct += 1
        else:
            score -= 10
        tally[q["category"]][1] += 1
        tally[q["category"]][0] += 1 if hit else 0
        wrong = [i for i in range(4) if i != q["answerIndex"]]
        if not hit and q["id"] in TRAPS and TRAPS[q["id"]] != q["answerIndex"]:
            wrong = [TRAPS[q["id"]]]   # 오답이 한 보기로 몰린다
        responses.append({
            "id": q["id"],
            "category": q["category"],
            "difficulty": q["difficulty"],
            "selectedIndex": q["answerIndex"] if hit else random.choice(wrong),
            "answerIndex": q["answerIndex"],
            "correct": hit,
            "delta": POINTS[q["difficulty"]] if hit else -10,
            "elapsedMs": random.randint(2500, 18000),
        })
    return {
        "schema": "quiz-record/v2",
        "name": name,
        "score": score,
        "correctCount": correct,
        "total": len(questions),
        "accuracy": correct / len(questions),
        "byCategory": {c: (v[0] / v[1] if v[1] else 0) for c, v in tally.items()},
        "playedAt": when.isoformat().replace("+00:00", "Z"),
        "durationMs": sum(r["elapsedMs"] for r in responses),
        "responses": responses,
    }, {q["id"] for q in questions}


def wrap(by, records):
    return {
        "schema": "quiz-export/v2",
        "app": "std03-quiz",
        "testData": True,
        "exportedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "exportedBy": by,
        "records": records,
    }


def write(name, payload):
    INBOX.mkdir(parents=True, exist_ok=True)
    path = INBOX / name
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  {name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bulk", type=int, default=0,
                    help="문항 분석을 시험하려고 1판짜리 참가자를 이만큼 더 만든다")
    ap.add_argument("--seed", type=int, default=20260901, help="난수 시드")
    ap.add_argument("--trap", default="",
                    help="이 문항을 '정답이 둘'인 것처럼 망가뜨린다 (예: SC-045)")
    args = ap.parse_args()

    random.seed(args.seed)  # 재현 가능하게
    bank = load_bank()

    if args.trap:
        q = next((x for x in bank if x["id"] == args.trap), None)
        if q is None:
            print(f"❌ 문항 {args.trap}을 찾지 못했다.")
            return 1
        TRAPS[q["id"]] = (q["answerIndex"] + 1) % 4
        print(f"함정 문항: {args.trap} → 보기{TRAPS[q['id']] + 1}로 몰아감\n")

    if args.bulk:
        # 노출을 쌓기 위한 대량 참가자. 이름이 전부 달라 라벨에 접미사가 붙지 않는다.
        big = []
        for i in range(args.bulk):
            rec, _ = play(bank, f"응시자{i + 1:03d}", random.uniform(.2, .85),
                          datetime(2026, 9, 2, tzinfo=timezone.utc) + timedelta(minutes=7 * i),
                          set())
            big.append(rec)
        write(f"TEST-대량{args.bulk}.json", wrap("대량", big))
        print(f"\n대량 {args.bulk}판 → {INBOX}")
        return 0
    t0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
    print("가짜 결과 파일 생성:")

    # ── 김철민: 3판 → 라벨 _1.._3 ─────────────────────────────
    seen, chulmin = set(), []
    for i, skill in enumerate([0.45, 0.55, 0.7]):
        rec, ids = play(bank, "김철민", skill, t0 + timedelta(minutes=25 * i), seen)
        seen = ids
        chulmin.append(rec)
    write("TEST-김철민-a.json", wrap("김철민", chulmin))

    # ── 같은 사람이 한 판 더 치고 다시 내보냄 → 앞 3판은 중복 ──
    extra, _ = play(bank, "김철민", 0.75, t0 + timedelta(minutes=100), seen)
    write("TEST-김철민-b.json", wrap("김철민", chulmin + [extra]))

    # ── 이수현: 1판 → 접미사 없이 '이수현' ────────────────────
    rec, _ = play(bank, "이수현", 0.62, t0 + timedelta(minutes=12), set())
    write("TEST-이수현.json", wrap("이수현", [rec]))

    # ── 박지훈: v1 1판 + v2 1판 → responses null 보존 확인 ────
    old, _ = play(bank, "박지훈", 0.5, t0 - timedelta(days=3), set())
    for key in ("schema", "responses", "durationMs"):
        old.pop(key)                       # 구버전 기록에는 이 셋이 없다
    new, _ = play(bank, "박지훈", 0.58, t0 + timedelta(minutes=40), set())
    write("TEST-박지훈.json", wrap("박지훈", [old, new]))

    # ── 최민서: 이름에 공백 → 정규화 후 같은 이름, 두 판 ──────
    a, _ = play(bank, " 최민서 ", 0.35, t0 + timedelta(minutes=5), set())
    b, _ = play(bank, "최민서", 0.4, t0 + timedelta(minutes=70), set())
    write("TEST-최민서.json", wrap("최민서", [a, b]))

    # ── 나머지 6명: 분포를 만들기 위한 1판씩 ──────────────────
    others = [("정하윤", .8), ("오세진", .3), ("한도현", .66), ("배수아", .5),
              ("문가온", .22), ("신유raw", .72)]
    solo = []
    for i, (nm, sk) in enumerate(others):
        rec, _ = play(bank, nm.replace("raw", "진"), sk,
                      t0 + timedelta(minutes=130 + 9 * i), set())
        solo.append(rec)
    write("TEST-단체.json", wrap("여러 명", solo))

    # ── 망가진 레코드가 섞인 파일 ─────────────────────────────
    good, _ = play(bank, "임재원", 0.6, t0 + timedelta(minutes=200), set())
    pct, _ = play(bank, "백분율", 0.5, t0 + timedelta(minutes=205), set())
    pct["accuracy"] = round(pct["accuracy"] * 100, 1)      # 백분율로 들어옴 → 고쳐야 함
    zero, _ = play(bank, "빵판", 0.5, t0 + timedelta(minutes=210), set())
    zero["total"] = 0                                      # 0으로 나눔 → 버려야 함
    over, _ = play(bank, "과다", 0.5, t0 + timedelta(minutes=215), set())
    over["correctCount"] = over["total"] + 5               # 말이 안 됨 → 버려야 함
    noname, _ = play(bank, "", 0.5, t0 + timedelta(minutes=220), set())
    noname["name"] = "   "                                 # 빈 이름 → 버려야 함
    write("TEST-엉망.json", wrap("엉망", [good, pct, zero, over, noname]))

    # ── 파일 자체가 잘못된 둘 ─────────────────────────────────
    write("TEST-깨진json.json", '{"schema": "quiz-export/v2", "records": [ {')
    write("TEST-다른앱.json", {"schema": "todo-export/v1", "items": [{"text": "우유"}]})

    print(f"\n총 {len(list(INBOX.glob('*.json')))}개 파일 → {INBOX}")


if __name__ == "__main__":
    sys.exit(main() or 0)
