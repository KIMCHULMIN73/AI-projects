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

# 분야별 문제 은행이 갖춰야 할 구성(요청 비율 10:20:40:20:10).
EXPECTED_BANK_MIX = {"top": 10, "high": 20, "mid": 40, "low": 20, "bottom": 10}
EXPECTED_PER_CATEGORY = sum(EXPECTED_BANK_MIX.values())

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


def pick_from_category(pool):
    by_difficulty = {d: [] for d in DIFFICULTY_ORDER}
    for question in pool:
        if question["difficulty"] in by_difficulty:
            by_difficulty[question["difficulty"]].append(question)
    for difficulty in DIFFICULTY_ORDER:
        random.shuffle(by_difficulty[difficulty])

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


def get_quiz_set(bank):
    quiz_set = []
    for category in CATEGORY_ORDER:
        quiz_set.extend(pick_from_category([q for q in bank if q["category"] == category]))
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
    expected_total = EXPECTED_PER_CATEGORY * len(CATEGORY_ORDER)
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

    print("\n── 분야별 문제 은행 구성 (최상10/상20/중40/하20/최하10) ──")
    by_category = defaultdict(Counter)
    for question in bank:
        by_category[question["category"]][question["difficulty"]] += 1
    for category in CATEGORY_ORDER:
        counts = by_category[category]
        total = sum(counts.values())
        ok = total == EXPECTED_PER_CATEGORY and all(
            counts[d] == EXPECTED_BANK_MIX[d] for d in EXPECTED_BANK_MIX
        )
        detail = f"총 {total} / " + " ".join(
            f"{d}:{counts[d]}" for d in ["top", "high", "mid", "low", "bottom"]
        )
        check(f"{category} 구성 일치", ok, detail)

    print("\n── getQuizSet() 시뮬레이션 (500회) ──")
    size_ok = order_ok = dup_ok = ratio_ok = True
    for _ in range(500):
        quiz_set = get_quiz_set(bank)
        if len(quiz_set) != QUESTIONS_PER_CATEGORY * len(CATEGORY_ORDER):
            size_ok = False
        if len({q["id"] for q in quiz_set}) != len(quiz_set):
            dup_ok = False
        for index, category in enumerate(CATEGORY_ORDER):
            block = quiz_set[index * QUESTIONS_PER_CATEGORY:(index + 1) * QUESTIONS_PER_CATEGORY]
            if any(q["category"] != category for q in block):
                order_ok = False
            counts = Counter(q["difficulty"] for q in block)
            if any(counts[d] != DIFFICULTY_QUOTA[d] for d in DIFFICULTY_QUOTA):
                ratio_ok = False

    check("항상 40문제", size_ok)
    check("세트 내 중복 없음", dup_ok)
    check("분야 블록 순서 = 한국사→과학→세계지리→예술", order_ok)
    check("분야마다 최상1/상2/중4/하2/최하1", ratio_ok)

    first = [q["id"] for q in get_quiz_set(bank)]
    second = [q["id"] for q in get_quiz_set(bank)]
    check("재호출 시 세트가 달라짐(재도전 대응)", first != second)

    print()
    if failures:
        print(f"❌ {len(failures)}건 실패: " + "; ".join(failures))
        return 1
    print("✅ 문제 은행 검증 전체 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
