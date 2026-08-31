#!/usr/bin/env python3
"""새 문항을 분야 파일의 알맞은 난이도 구간에 끼워 넣고 id를 다시 매긴다.

  python3 tools/insert_questions.py <분야> <난이도> <새문항.json> [--dry-run]

분야 파일은 쉬운 것부터 어려운 순(bottom → low → mid → high → top)으로 문항을
모아 두고 id를 파일 순서대로 오름차순 부여한다. 그래서 중간 구간에 문항을 넣으면
뒤쪽 문항의 id가 통째로 밀린다. 이 스크립트는 그 재부여를 손으로 하지 않게 한다.

새문항.json 형식 — 객체 배열. id/category/difficulty는 스크립트가 채우므로 쓰지 않는다.
  [{"question": "...", "choices": ["","","",""], "answerIndex": 0, "explanation": "..."}]

[개발용] 앱은 이 파일을 로드하지 않는다.
"""
import json
import re
import unicodedata
import sys
from collections import Counter
from pathlib import Path

BANK_DIR = Path(__file__).resolve().parent.parent / "data" / "questions"
REPO_ROOT = BANK_DIR.parent.parent.parent

STEMS = {
    "korean-history": "KH",
    "science": "SC",
    "world-geography": "WG",
    "arts-culture": "AC",
}
ALIAS = {
    "korean-history": "korean-history", "korean_history": "korean-history",
    "한국사": "korean-history", "kh": "korean-history",
    "science": "science", "과학": "science", "sc": "science",
    "world-geography": "world-geography", "world_geography": "world-geography",
    "세계지리": "world-geography", "지리": "world-geography", "wg": "world-geography",
    "arts-culture": "arts-culture", "arts_culture": "arts-culture",
    "예술": "arts-culture", "문화": "arts-culture", "시사": "arts-culture",
    "ac": "arts-culture",
}
DIF_ALIAS = {
    "top": "top", "최상": "top",
    "high": "high", "상": "high",
    "mid": "mid", "중": "mid",
    "low": "low", "하": "low",
    "bottom": "bottom", "최하": "bottom",
}
# 파일에 놓이는 순서. storage.js의 DIFFICULTY_ORDER와 같은 방향(쉬움 → 어려움)이다.
FILE_ORDER = ["bottom", "low", "mid", "high", "top"]
# validate_bank.py는 분야 키를 밑줄 표기로 쓴다(파일명은 하이픈).
VALIDATOR_KEY = {s: s.replace("-", "_") for s in STEMS}
DIF_LABEL = {"bottom": "최하", "low": "하", "mid": "중", "high": "상", "top": "최상"}

OBJ = re.compile(r"  \{\n    id: '([^']+)',\n(?:.*?)\n  \},\n", re.S)
BLOCK_HDR = re.compile(r"^  // ── (최하|하|중|상|최상)\((\w+)\) (\d+) ──(.*)$", re.M)
MIX_HDR = re.compile(r"^// 난이도 구성: .*$", re.M)


def die(msg, code=1):
    print(f"✗ {msg}")
    sys.exit(code)


def width(s):
    """터미널·에디터 기준 표시 폭. 한글·한자는 두 칸을 먹는다."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def js_str(s):
    """파일 관례에 맞춘 JS 문자열 리터럴. 작은따옴표가 들어 있으면 큰따옴표로 감싼다."""
    if "'" not in s:
        return "'" + s.replace("\\", "\\\\") + "'"
    if '"' not in s:
        return '"' + s.replace("\\", "\\\\") + '"'
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def norm(s):
    """중복 판정을 위한 정규화 — 공백·문장부호를 지우고 비교한다."""
    return re.sub(r"[\s?!.,·'\"()\[\]「」『』‘’“”-]", "", s)


def render(q, qid, difficulty):
    choices = ", ".join(js_str(c) for c in q["choices"])
    one_line = f"    choices: [{choices}],"
    if width(one_line) > 100:
        body = "\n".join(f"      {js_str(c)}," for c in q["choices"])
        choices_block = f"    choices: [\n{body}\n    ],"
    else:
        choices_block = one_line
    return (
        "  {\n"
        f"    id: '{qid}',\n"
        "    category: C,\n"
        f"    difficulty: '{difficulty}',\n"
        f"    question: {js_str(q['question'])},\n"
        f"{choices_block}\n"
        f"    answerIndex: {q['answerIndex']},\n"
        f"    explanation: {js_str(q['explanation'])},\n"
        "  },\n"
    )


def parse_bank():
    """전 분야 문항을 읽는다 — 중복 검사는 은행 전체를 상대로 해야 한다."""
    bank = []
    for stem in STEMS:
        src = (BANK_DIR / f"{stem}.js").read_text(encoding="utf-8")
        for m in OBJ.finditer(src):
            block = m.group(0)
            qm = re.search(r"\n    question: ((\"(?:[^\"\\]|\\.)*\")|('(?:[^'\\]|\\.)*'))", block)
            bank.append({
                "id": m.group(1),
                "stem": stem,
                "difficulty": re.search(r"difficulty: '(\w+)'", block).group(1),
                "question": qm.group(1)[1:-1],
                "choices": re.findall(r"(?:\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)')",
                                      re.search(r"\n    choices: \[(.*?)\],\n", block, re.S).group(1)),
            })
    return bank


def check_new(items, bank):
    """CLAUDE.md 「퀴즈 문제 교차 검증 가이드라인」 중 기계로 볼 수 있는 것만 본다.
    내용의 옳고 그름(4번)과 기준 명시(2·3번)는 사람이 봐야 한다."""
    errs = []
    seen = {}
    for q in bank:
        seen[norm(q["question"])] = q["id"]

    for i, q in enumerate(items):
        tag = f"새 문항 #{i + 1}"
        for key in ("question", "choices", "answerIndex", "explanation"):
            if key not in q:
                errs.append(f"{tag}: '{key}' 누락")
        if errs and any(tag in e for e in errs):
            continue
        ok4 = isinstance(q["choices"], list) and len(q["choices"]) == 4
        if not ok4:
            errs.append(f"{tag}: choices는 정확히 4개여야 한다 (지금 {len(q.get('choices', []))}개)")
        if not isinstance(q["answerIndex"], int) or not 0 <= q["answerIndex"] <= 3:
            errs.append(f"{tag}: answerIndex는 0~3이어야 한다 (지금 {q['answerIndex']!r})")
        if ok4 and len(set(q["choices"])) != 4:
            dup = [c for c, n in Counter(q["choices"]).items() if n > 1]
            errs.append(f"{tag}: 한 문항 안에 같은 보기가 있다 → {', '.join(dup)}")
        if not str(q["question"]).strip():
            errs.append(f"{tag}: question이 비었다")
        if not str(q["explanation"]).strip():
            errs.append(f"{tag}: explanation이 비었다")
        key = norm(q["question"])
        if key in seen:
            errs.append(f"{tag}: 기존 {seen[key]}와 질문이 같다 → {q['question']}")
        seen[key] = tag
    return errs


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv[1:]
    if len(args) != 3:
        die("사용법: insert_questions.py <분야> <난이도> <새문항.json> [--dry-run]", 2)

    stem = ALIAS.get(args[0].lower())
    if not stem:
        die(f"분야를 알 수 없습니다: {args[0]!r}  (한국사 | 과학 | 세계지리 | 예술)", 2)
    difficulty = DIF_ALIAS.get(args[1].lower())
    if not difficulty:
        die(f"난이도를 알 수 없습니다: {args[1]!r}  (최상 | 상 | 중 | 하 | 최하)", 2)

    path = Path(args[2])
    if not path.exists():
        die(f"파일이 없습니다: {path}", 2)
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"JSON을 읽지 못했습니다: {e}", 2)
    if not isinstance(items, list) or not items:
        die("JSON은 비어 있지 않은 객체 배열이어야 합니다.", 2)

    bank = parse_bank()
    errs = check_new(items, bank)
    if errs:
        print("✗ 새 문항에 문제가 있습니다. 고친 뒤 다시 돌리세요.\n")
        for e in errs:
            print(f"   {e}")
        sys.exit(1)

    target = BANK_DIR / f"{stem}.js"
    src = target.read_text(encoding="utf-8")
    prefix = STEMS[stem]

    # ── 삽입 위치: 해당 난이도 구간의 마지막 문항 뒤 ────────────────────
    hdrs = {m.group(2): m for m in BLOCK_HDR.finditer(src)}
    if difficulty not in hdrs:
        die(f"{target.name}에 {difficulty} 구간 주석이 없습니다. 파일을 손으로 확인하세요.")
    nxt = FILE_ORDER[FILE_ORDER.index(difficulty) + 1:]
    following = [hdrs[d].start() for d in nxt if d in hdrs]
    # 구간 끝 = 다음 구간 주석 앞의 빈 줄 위치, 마지막 구간이면 배열 닫기 직전
    end = min(following) if following else src.rindex("];")
    while src[end - 1] == "\n":
        end -= 1
    end += 1  # 마지막 문항의 개행 바로 뒤

    old_ids = [m.group(1) for m in OBJ.finditer(src)]
    at = len(re.findall(r"\n    id: '", src[:end]))  # 삽입 지점 앞의 문항 수

    added = "".join(render(q, f"{prefix}-XXX", difficulty) for q in items)
    src = src[:end] + added + src[end:]

    # ── id 재부여: 파일에 놓인 순서대로 001부터 ──────────────────────────
    counter = iter(range(1, 10_000))
    new_ids = []

    def renum(m):
        n = next(counter)
        new_ids.append(f"{prefix}-{n:03d}")
        return f"id: '{prefix}-{n:03d}',"

    src = re.sub(r"id: '(?:%s)-[^']*',\n" % prefix, lambda m: renum(m) + "\n", src)

    # ── 머리말의 개수 갱신 ──────────────────────────────────────────────
    counts = Counter(m.group(1) for m in re.finditer(r"difficulty: '(\w+)'", src))

    def fix_hdr(m):
        return f"  // ── {m.group(1)}({m.group(2)}) {counts[m.group(2)]} ──{m.group(4)}"

    src = BLOCK_HDR.sub(fix_hdr, src)
    src = MIX_HDR.sub(
        "// 난이도 구성: " + " / ".join(
            f"{DIF_LABEL[d]}({d}) {counts[d]}" for d in FILE_ORDER),
        src, count=1)
    title = re.sub(r"(\d+)문항", f"{sum(counts.values())}문항", src.split("\n")[0], count=1)
    src = title + src[src.index("\n"):]

    # ── 보고 ────────────────────────────────────────────────────────────
    shifted = [(o, n) for o, n in zip(old_ids[at:], new_ids[at + len(items):]) if o != n]
    print(f"분야     : {stem}  ({DIF_LABEL[difficulty]}/{difficulty})")
    print(f"새 문항  : {len(items)}개  →  {new_ids[at]} ~ {new_ids[at + len(items) - 1]}")
    print(f"총 문항  : {len(old_ids)} → {sum(counts.values())}")
    print("구성     : " + " ".join(f"{d}:{counts[d]}" for d in FILE_ORDER))

    if shifted:
        print(f"\n밀린 id  : {len(shifted)}개  ({shifted[0][0]}→{shifted[0][1]} … "
              f"{shifted[-1][0]}→{shifted[-1][1]})")
        docs = [p for p in [REPO_ROOT / "CLAUDE.md", *(REPO_ROOT / ".claude").rglob("*.md")]
                if p.is_file()]
        text = {p: p.read_text(encoding="utf-8") for p in docs}
        stale = [(old, new, [p.name for p in docs if old in text[p]])
                 for old, new in shifted]
        stale = [row for row in stale if row[2]]
        if stale:
            print("\n⚠ 문서가 예전 id를 가리키게 됩니다 — 손으로 고칠 것:")
            for old, new, files in stale:
                print(f"   {old} → {new}   {', '.join(Path(f).name for f in files)}")
    else:
        print("\n밀린 id  : 없음 (마지막 난이도 구간이라 뒤에 문항이 없다)")

    if dry:
        print("\n--dry-run: 파일을 쓰지 않았습니다.")
        return
    target.write_text(src, encoding="utf-8")
    print(f"\n✓ {target.relative_to(REPO_ROOT)} 갱신 완료")

    # validate_bank.py의 기대 구성표도 함께 올린다. 이걸 빼먹으면 검증기가
    # "구성 일치 FAIL"을 내는데, 실제로는 의도한 증가라 오해를 부른다.
    vpath = Path(__file__).resolve().parent / "validate_bank.py"
    vsrc = vpath.read_text(encoding="utf-8")
    key = VALIDATOR_KEY[stem]
    row = re.compile(r'^(    "%s": )\{[^}]*\},$' % key, re.M)
    if not row.search(vsrc):
        print("⚠ validate_bank.py의 EXPECTED_BANK_MIX에서 "
              f"'{key}' 줄을 찾지 못했습니다 — 손으로 갱신하세요.")
    else:
        mix = "{" + ", ".join(f'"{d}": {counts[d]}'
                              for d in ["top", "high", "mid", "low", "bottom"]) + "},"
        vpath.write_text(row.sub(lambda m: m.group(1) + mix, vsrc, count=1), encoding="utf-8")
        print(f"✓ tools/validate_bank.py의 기대 구성표 갱신 ({key} → 총 {sum(counts.values())})")

    print("\n  이어서: python3 tools/validate_bank.py")


if __name__ == "__main__":
    main()
