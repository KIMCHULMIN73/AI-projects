#!/usr/bin/env python3
"""문제 은행(data/questions.js) 검증기 — 개발 전용.

이 환경에는 Node/Deno 같은 JS 런타임이 없어서 storage.js를 직접 실행할 수 없다.
대신 questions.js를 파싱해 (1) 스키마 무결성과 (2) getQuizSet()이 요구하는
난이도 할당량을 충족할 수 있는지를 검사하고, storage.js의 샘플링 알고리즘을
그대로 옮겨 시뮬레이션해 분포를 확인한다.

앱은 이 파일을 로드하지 않는다. 문항을 추가한 뒤 다음처럼 돌려 보면 된다:

    python3 tools/validate_bank.py
"""

import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

BANK_DIR = Path(__file__).resolve().parent.parent / "data" / "questions"

# 문항 파일 → 그 파일이 담당하는 분야. 각 파일은 category를 상수 C로 묶어 두므로
# 파일명에서 분야를 알아내야 한다(파일 안에 분야 문자열이 한 번만 나온다).
CATEGORY_FILES = {
    "korean_history": "korean-history.js",
    "science": "science.js",
    "world_geography": "world-geography.js",
    "arts_culture": "arts-culture.js",
}

CATEGORY_ORDER = ["korean_history", "science", "world_geography", "arts_culture"]
DIFFICULTY_ORDER = ["bottom", "low", "mid", "high", "top"]  # storage.js와 동일한 순서
DIFFICULTY_QUOTA = {"top": 1, "high": 2, "mid": 4, "low": 2, "bottom": 1}
QUESTIONS_PER_CATEGORY = sum(DIFFICULTY_QUOTA.values())

# 분야별 문제 은행이 갖춰야 할 구성. 처음에는 네 분야 모두 요청 비율 10:20:40:20:10이었다.
# /quiz-add로 한 난이도만 늘리면 분야마다 구성이 달라지므로 분야별로 적어 둔다.
# 문항을 추가하면 이 표도 함께 갱신해야 한다 — tools/insert_questions.py가 자동으로 고친다.
# 비율이 달라져도 샘플링은 깨지지 않는다(DIFFICULTY_QUOTA만 채우면 된다). 이 검사는
# "의도치 않게 구성이 흘러내렸는지"를 보는 것이므로, 의도적으로 늘렸으면 표를 고치는 게 맞다.
EXPECTED_BANK_MIX = {
    "korean_history": {"top": 10, "high": 20, "mid": 40, "low": 20, "bottom": 10},
    "science": {"top": 10, "high": 30, "mid": 40, "low": 20, "bottom": 10},
    "world_geography": {"top": 10, "high": 20, "mid": 40, "low": 20, "bottom": 10},
    "arts_culture": {"top": 10, "high": 20, "mid": 40, "low": 20, "bottom": 10},
}

OBJECT_RE = re.compile(r"\{\s*\n\s*id:\s*'([^']+)',(.*?)\n  \},", re.S)
FIELD_RE = {
    "difficulty": re.compile(r"\n\s*difficulty:\s*'([^']+)'"),
    "answerIndex": re.compile(r"\n\s*answerIndex:\s*(\d+)"),
}
QUESTION_RE = re.compile(r"\n\s*question:\s*(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')")
EXPLANATION_RE = re.compile(r"\n\s*explanation:\s*(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')")
CHOICES_RE = re.compile(r"\n\s*choices:\s*\[(.*?)\],", re.S)
STRING_RE = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'")


def parse_bank(source: str, category: str):
    """분야 파일 하나에서 문항 객체를 뽑아낸다. category는 파일 단위로 주어진다."""
    questions = []
    for match in OBJECT_RE.finditer(source):
        qid, body = match.group(1), match.group(2)
        entry = {"id": qid, "category": category}
        for key, pattern in FIELD_RE.items():
            found = pattern.search(body)
            entry[key] = found.group(1) if found else None
        entry["answerIndex"] = int(entry["answerIndex"]) if entry["answerIndex"] else None

        for key, pattern in (("question", QUESTION_RE), ("explanation", EXPLANATION_RE)):
            found = pattern.search(body)
            entry[key] = found.group(1)[1:-1] if found else ""

        found = CHOICES_RE.search(body)
        entry["choices"] = STRING_RE.findall(found.group(1)) if found else []
        questions.append(entry)
    return questions


# ── storage.js의 샘플링 알고리즘 이식 ──────────────────────────────
def nearest_available(by_difficulty, target):
    target_rank = DIFFICULTY_ORDER.index(target)
    best, best_distance = None, float("inf")
    for rank, difficulty in enumerate(DIFFICULTY_ORDER):
        if not by_difficulty[difficulty]:
            continue
        distance = abs(rank - target_rank)
        if distance < best_distance:
            best, best_distance = difficulty, distance
    return best


def pick_from_category(pool, exclude):
    by_difficulty = {d: [] for d in DIFFICULTY_ORDER}
    for question in pool:
        if question["difficulty"] in by_difficulty:
            by_difficulty[question["difficulty"]].append(question)
    # 직전 판에 나온 문항은 난이도 안에서 뒤로 미룬다(제외가 아니라 후순위).
    for difficulty in DIFFICULTY_ORDER:
        bucket = by_difficulty[difficulty]
        fresh = [q for q in bucket if q["id"] not in exclude]
        used = [q for q in bucket if q["id"] in exclude]
        random.shuffle(fresh)
        random.shuffle(used)
        by_difficulty[difficulty] = fresh + used

    picked, shortfalls = [], []
    for difficulty in DIFFICULTY_ORDER:
        need = DIFFICULTY_QUOTA.get(difficulty, 0)
        taken = by_difficulty[difficulty][:need]
        del by_difficulty[difficulty][:need]
        picked.extend(taken)
        if len(taken) < need:
            shortfalls.append((difficulty, need - len(taken)))

    for difficulty, missing in shortfalls:
        for _ in range(missing):
            donor = nearest_available(by_difficulty, difficulty)
            if donor is None:
                break
            picked.append(by_difficulty[donor].pop(0))

    random.shuffle(picked)
    return picked


def get_quiz_set(bank, exclude=frozenset()):
    quiz_set = []
    for category in CATEGORY_ORDER:
        quiz_set.extend(
            pick_from_category([q for q in bank if q["category"] == category], exclude)
        )
    random.shuffle(quiz_set)  # 출제 순서는 분야를 가로질러 섞는다
    return quiz_set


def main():
    bank = []
    for category in CATEGORY_ORDER:
        path = BANK_DIR / CATEGORY_FILES[category]
        bank.extend(parse_bank(path.read_text(encoding="utf-8"), category))
    failures = []

    def check(label, ok, extra=""):
        print(f"{'PASS' if ok else 'FAIL'}  {label}{('  → ' + extra) if extra else ''}")
        if not ok:
            failures.append(label)

    print(f"── 문제 은행 무결성 ({len(bank)}문항) ──")
    expected_total = sum(sum(mix.values()) for mix in EXPECTED_BANK_MIX.values())
    check(f"전체 {expected_total}문항", len(bank) == expected_total, f"{len(bank)}문항")

    ids = [q["id"] for q in bank]
    duplicates = [i for i, n in Counter(ids).items() if n > 1]
    check("id 중복 없음", not duplicates, ", ".join(duplicates))

    bad_choices = [q["id"] for q in bank if len(q["choices"]) != 4]
    check("모든 문항 보기 4개", not bad_choices, ", ".join(bad_choices))

    bad_answer = [q["id"] for q in bank if q["answerIndex"] is None or not 0 <= q["answerIndex"] <= 3]
    check("answerIndex 0~3", not bad_answer, ", ".join(bad_answer))

    dup_choices = [q["id"] for q in bank if len(set(q["choices"])) != len(q["choices"])]
    check("한 문항 안에 같은 보기 없음", not dup_choices, ", ".join(dup_choices))

    bad_category = [q["id"] for q in bank if q["category"] not in CATEGORY_ORDER]
    check("category 값 유효", not bad_category, ", ".join(bad_category))

    bad_difficulty = [q["id"] for q in bank if q["difficulty"] not in DIFFICULTY_ORDER]
    check("difficulty 값 유효", not bad_difficulty, ", ".join(bad_difficulty))

    no_text = [q["id"] for q in bank if not q["question"].strip()]
    check("질문 텍스트 존재", not no_text, ", ".join(no_text))

    no_explanation = [q["id"] for q in bank if not q["explanation"].strip()]
    check("해설 존재(전 문항 필수)", not no_explanation, ", ".join(no_explanation))

    print("\n── 분야별 문제 은행 구성 (EXPECTED_BANK_MIX와 대조) ──")
    by_category = defaultdict(Counter)
    for question in bank:
        by_category[question["category"]][question["difficulty"]] += 1
    for category in CATEGORY_ORDER:
        counts = by_category[category]
        total = sum(counts.values())
        mix = EXPECTED_BANK_MIX[category]
        ok = total == sum(mix.values()) and all(counts[d] == mix[d] for d in mix)
        detail = f"총 {total} / " + " ".join(
            f"{d}:{counts[d]}" for d in ["top", "high", "mid", "low", "bottom"]
        )
        check(f"{category} 구성 일치", ok, detail)

    print("\n── getQuizSet() 시뮬레이션 (500회) ──")
    size_ok = dup_ok = ratio_ok = per_category_ok = True
    blocked_runs = 0  # 분야가 블록째로 뭉쳐 나온 판의 수
    for _ in range(500):
        quiz_set = get_quiz_set(bank)
        if len(quiz_set) != QUESTIONS_PER_CATEGORY * len(CATEGORY_ORDER):
            size_ok = False
        if len({q["id"] for q in quiz_set}) != len(quiz_set):
            dup_ok = False

        for category in CATEGORY_ORDER:
            block = [q for q in quiz_set if q["category"] == category]
            if len(block) != QUESTIONS_PER_CATEGORY:
                per_category_ok = False
            counts = Counter(q["difficulty"] for q in block)
            if any(counts[d] != DIFFICULTY_QUOTA[d] for d in DIFFICULTY_QUOTA):
                ratio_ok = False

        # 섞였는지 확인: 이웃한 문제의 분야가 바뀌는 횟수가 3(=블록 배치)뿐이면 안 섞인 것이다.
        switches = sum(
            1 for a, b in zip(quiz_set, quiz_set[1:]) if a["category"] != b["category"]
        )
        if switches <= len(CATEGORY_ORDER) - 1:
            blocked_runs += 1

    check("항상 40문제", size_ok)
    check("세트 내 중복 없음", dup_ok)
    check("분야별 10문제씩", per_category_ok)
    check("분야마다 최상1/상2/중4/하2/최하1", ratio_ok)
    check("출제 순서가 분야를 가로질러 섞임", blocked_runs == 0, f"블록 배치 {blocked_runs}/500회")

    first = [q["id"] for q in get_quiz_set(bank)]
    second = [q["id"] for q in get_quiz_set(bank)]
    check("재호출 시 세트가 달라짐(재도전 대응)", first != second)

    print("\n── 직전 판 중복 회피 (연속 2판 × 200회) ──")
    overlaps, ratio_kept = [], True
    for _ in range(200):
        prev = get_quiz_set(bank)
        prev_ids = {q["id"] for q in prev}
        nxt = get_quiz_set(bank, exclude=prev_ids)
        overlaps.append(len(prev_ids & {q["id"] for q in nxt}))
        for category in CATEGORY_ORDER:
            counts = Counter(
                q["difficulty"] for q in nxt if q["category"] == category
            )
            if any(counts[d] != DIFFICULTY_QUOTA[d] for d in DIFFICULTY_QUOTA):
                ratio_kept = False

    worst = max(overlaps)
    check("직전 판과 겹치는 문항 0개", worst == 0, f"최대 {worst}개 / 평균 {sum(overlaps)/len(overlaps):.2f}개")
    check("회피하면서도 난이도 비율 유지", ratio_kept)

    print()
    if failures:
        print(f"❌ {len(failures)}건 실패: " + "; ".join(failures))
        return 1
    print("✅ 문제 은행 검증 전체 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
