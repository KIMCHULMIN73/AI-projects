#!/usr/bin/env python3
"""[선생님 모드 · 2단계] 흩어진 결과 파일을 묶음 하나로 모은다 — 개발 전용.

data/results/inbox/*.json (quiz-export/v2) 여러 개를 읽어
data/results/results.json (quiz-results/v1) 하나로 합친다.
3~5단계는 이 파일 하나만 본다.

**세는 단위: 판 하나가 참가자 하나다.**
같은 이름으로 여러 번 쳐도 각각 다른 사람으로 센다 — 2회 이상이면
playedAt 오름차순으로 이름_1, 이름_2 …, 1회뿐이면 접미사를 붙이지 않는다.
그래서 "철민과 김철민이 같은 사람인가"를 판정할 일이 아예 없다.

같은 판이 두 파일에 들어오는 것(내보내기를 두 번 누른 경우)은 별개 문제라
name + playedAt으로 걸러 낸다. 다른 playedAt = 다른 판 = 다른 참가자,
같은 playedAt = 같은 판이 두 번 들어온 것.

    python3 tools/results_collect.py [--dry-run] [--strict]
"""

import argparse
import json
import shutil
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "data" / "results"
INBOX = RESULTS_DIR / "inbox"
ARCHIVE = RESULTS_DIR / "archive"
OUT = RESULTS_DIR / "results.json"

SCHEMA = "quiz-results/v1"
CATEGORY_ORDER = ["korean_history", "science", "world_geography", "arts_culture"]


# ── 표 정렬 (한글 폭 계산) — /quiz-stats와 같은 방식 ───────────────
def w(t):
    return sum(2 if unicodedata.east_asian_width(str(c)) in "WF" else 1 for c in str(t))


def pad(t, n, right=False):
    sp = " " * max(0, n - w(t))
    return sp + str(t) if right else str(t) + sp


def norm_name(raw):
    """표기를 다듬는 데까지만. 다른 이름을 같은 사람으로 미루어 짐작하지 않는다."""
    s = unicodedata.normalize("NFKC", str(raw or ""))
    return " ".join(s.split())


def parse_time(v):
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def check_record(rec, warnings, where):
    """레코드 하나를 검사·보정한다. 못 쓰면 사유 문자열을 돌려준다."""
    if not isinstance(rec, dict):
        return "레코드가 객체가 아님"

    name = norm_name(rec.get("name"))
    if not name:
        return "이름이 비어 있음"
    if parse_time(rec.get("playedAt")) is None:
        return "playedAt이 시각이 아님"

    try:
        score = int(rec["score"])
        correct = int(rec["correctCount"])
        total = int(rec["total"])
    except (KeyError, TypeError, ValueError):
        return "score/correctCount/total이 정수가 아님"
    if total <= 0:
        return "total이 0 이하 (정답률을 계산할 수 없음)"
    if not 0 <= correct <= total:
        return f"correctCount({correct})가 total({total}) 범위를 벗어남"

    acc = rec.get("accuracy")
    try:
        acc = float(acc)
    except (TypeError, ValueError):
        acc = None
    if acc is not None and acc > 1:
        # 백분율로 들어온 것으로 보고 고친다.
        warnings.append(f"{where}: {name} accuracy가 {acc} — 백분율로 보고 100으로 나눔")
        acc /= 100
    if acc is None or not 0 <= acc <= 1:
        warnings.append(f"{where}: {name} accuracy를 correctCount/total로 다시 계산")
        acc = correct / total

    by = rec.get("byCategory")
    if not isinstance(by, dict):
        by = {}
    by = {k: float(v) for k, v in by.items() if isinstance(v, (int, float))}
    if any(not 0 <= v <= 1 for v in by.values()):
        return "byCategory 값이 0..1을 벗어남"

    # responses: **없는 것과 빈 배열은 다르다.** 없으면 null로 남겨야
    # 3~4단계가 "총점만 있는 구버전 기록"으로 알아본다. []로 뭉개면
    # 그 판이 "전부 오답"으로 계산되어 평균이 통째로 거짓말을 한다.
    resp = rec.get("responses")
    if resp is None:
        resp = None
    elif isinstance(resp, list):
        if not all(isinstance(x, dict) and "id" in x and "correct" in x for x in resp):
            return "responses 항목에 id/correct가 없음"
        if len(resp) != total:
            warnings.append(
                f"{where}: {name} responses {len(resp)}개 ≠ total {total} (중간 이탈?)"
            )
    else:
        return "responses가 배열이 아님"

    return {
        "name": name,
        "score": score,
        "correctCount": correct,
        "total": total,
        "accuracy": acc,
        "byCategory": by,
        "playedAt": rec["playedAt"],
        "durationMs": rec.get("durationMs"),
        "responses": resp,
    }


def make_labels(records):
    """판 하나 = 참가자 하나. 2회 이상인 이름만 playedAt 순으로 _1..붙인다."""
    raw_names = {r["name"] for r in records}
    by_name = {}
    for r in records:
        by_name.setdefault(r["name"], []).append(r)

    used = set()
    for name, group in by_name.items():
        group.sort(key=lambda r: parse_time(r["playedAt"]))
        for i, rec in enumerate(group, 1):
            label = name if len(group) == 1 else f"{name}_{i}"
            # 만들어 낸 라벨이 실제로 존재하는 다른 이름과 겹치면 비켜 준다.
            while label in used or (label != name and label in raw_names):
                label += "_"
            rec["label"] = label
            used.add(label)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="반영 없이 결과만 본다")
    ap.add_argument("--strict", action="store_true", help="경고도 실패로 친다")
    args = ap.parse_args()

    if not INBOX.exists():
        INBOX.mkdir(parents=True, exist_ok=True)
    files = sorted(INBOX.glob("*.json"))
    if not files and not OUT.exists():
        print("inbox가 비어 있고 results.json도 없다.")
        print(f"결과 파일을 {INBOX} 에 넣고 다시 돌릴 것.")
        return 1

    warnings, sources, records = [], [], []
    seen = {}  # (name, playedAt) → 어느 파일에서 처음 왔는지

    # 이미 있는 묶음을 먼저 싣는다(누적이 원칙 — inbox는 새로 온 것만 담는다).
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
            for r in prev.get("records", []):
                r.pop("label", None)
                r.pop("participantId", None)
                records.append(r)
                seen[(r["name"], r["playedAt"])] = "(기존 results.json)"
            print(f"기존 묶음에서 {len(records)}판을 이어받았다.")
        except (OSError, ValueError, KeyError) as e:
            print(f"⚠️  기존 results.json을 읽지 못해 새로 만든다: {e}")
            records, seen = [], {}

    for path in files:
        stat = {"file": path.name, "records": 0, "added": 0, "duplicate": 0, "invalid": 0}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            stat["invalid"] = 1
            warnings.append(f"{path.name}: JSON을 읽지 못함 ({e}) — 파일 전체를 건너뜀")
            sources.append(stat)
            continue

        if not str(data.get("schema", "")).startswith("quiz-export/"):
            stat["invalid"] = 1
            warnings.append(
                f"{path.name}: schema가 '{data.get('schema')}' — 이 앱의 반출 파일이 아님"
            )
            sources.append(stat)
            continue

        raw = data.get("records") or []
        stat["records"] = len(raw)
        for rec in raw:
            checked = check_record(rec, warnings, path.name)
            if isinstance(checked, str):
                stat["invalid"] += 1
                warnings.append(f"{path.name}: 레코드 버림 — {checked}")
                continue
            key = (checked["name"], checked["playedAt"])
            if key in seen:
                stat["duplicate"] += 1
                continue
            seen[key] = path.name
            records.append(checked)
            stat["added"] += 1
        sources.append(stat)

    if not records:
        print("쓸 수 있는 레코드가 하나도 없다.")
        return 1

    make_labels(records)
    records.sort(key=lambda r: (r["label"],))
    participants = []
    for i, r in enumerate(sorted(records, key=lambda x: x["label"]), 1):
        pid = f"p{i:03d}"
        r["participantId"] = pid
        participants.append({
            "id": pid, "label": r["label"], "name": r["name"], "playedAt": r["playedAt"],
        })

    out = {
        "schema": SCHEMA,
        "mergedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "participants": participants,
        "records": [
            {"participantId": r["participantId"], "label": r["label"], **{
                k: v for k, v in r.items() if k not in ("participantId", "label")}}
            for r in records
        ],
        "sources": sources,
        "warnings": warnings,
    }

    # ── 보고 ──
    print("\n파일별 처리")
    hdr = ["파일", "레코드", "추가", "중복", "버림"]
    widths = [max(w(hdr[0]), *(w(s["file"]) for s in sources)) if sources else 4, 6, 4, 4, 4]
    print("  " + pad(hdr[0], widths[0]) + "".join(
        " " + pad(h, wd, right=True) for h, wd in zip(hdr[1:], widths[1:])))
    for s in sources:
        print("  " + pad(s["file"], widths[0]) + "".join(
            " " + pad(s[k], wd, right=True)
            for k, wd in zip(("records", "added", "duplicate", "invalid"), widths[1:])))

    by_name = {}
    for r in records:
        by_name.setdefault(r["name"], []).append(r)
    multi = {n: len(v) for n, v in by_name.items() if len(v) > 1}
    v2 = sum(1 for r in records if isinstance(r["responses"], list))
    print(f"\n참가자 {len(records)}명 / 서로 다른 이름 {len(by_name)}개")
    if multi:
        print("  여러 번 친 이름: " + ", ".join(f"{k}×{v}" for k, v in sorted(multi.items())))
    print(f"  문항분석 가능 {v2}판 / 총점만 {len(records) - v2}판")
    if warnings:
        print(f"\n경고 {len(warnings)}건")
        for m in warnings:
            print("  ⚠️  " + m)

    if args.dry_run:
        print("\n--dry-run — 아무것도 쓰지 않았다.")
        return 0

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    gitignore = RESULTS_DIR / ".gitignore"
    if not gitignore.exists():
        # 자기 자신까지 무시한다. !.gitignore 예외를 두면 추적되지 않은 파일이
        # 남아 폴더가 git status에 노출된다(tools/.backups/와 같은 방식).
        gitignore.write_text("*\n", encoding="utf-8")
    if OUT.exists():
        ARCHIVE.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(OUT, ARCHIVE / f"results-{stamp}.json")
        print(f"\n직전 묶음을 archive/results-{stamp}.json 으로 옮겨 뒀다.")
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{OUT} 기록 완료.")

    if args.strict and warnings:
        print("--strict — 경고가 있어 실패로 처리한다.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
