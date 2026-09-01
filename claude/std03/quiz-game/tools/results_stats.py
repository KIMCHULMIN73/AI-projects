#!/usr/bin/env python3
"""[선생님 모드 · 3단계] 결과 묶음의 통계를 계산한다 — 개발 전용.

data/results/results.json → data/results/analysis.json (quiz-analysis/v1)

**계산은 이 파일 한 곳에만 있다.** 대시보드는 analysis.json을 그리기만 한다.
JS로도 짜면 validate_bank.py ↔ storage.js가 이미 겪는 이중 구현이 재현되고,
갈라진 순간 화면과 터미널이 서로 다른 1등을 말한다.

알려 주는 것은 하나다 — **전체 안에서 내가 어디쯤인가.**
전체 인원·평균·표준편차가 있으면 z-점수·백분위·석차가 나온다.
제도적 등급 같은 칸 나누기는 두지 않는다.

**판 하나가 참가자 하나다.** 같은 이름이 여러 번 쳤으면 김철민_1·김철민_2가
각각 한 명으로 센다(2단계에서 정해진다). 그래서 "여러 판 중 무엇을 그 사람
성적으로 볼 것인가" 같은 문제가 이 단계에는 아예 없다.

    python3 tools/results_stats.py [--top N] [--anon]
"""

import argparse
import json
import statistics as st
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "data" / "results" / "results.json"
OUT_DEFAULT = ROOT / "data" / "results" / "analysis.json"

CATEGORY_ORDER = ["korean_history", "science", "world_geography", "arts_culture"]
CATEGORY_LABEL = {
    "korean_history": "한국사 상식", "science": "과학 상식",
    "world_geography": "세계지리 상식", "arts_culture": "예술·문화·시사",
}
DIFFICULTY_ORDER = ["bottom", "low", "mid", "high", "top"]
DIFFICULTY_LABEL = {"bottom": "최하", "low": "하", "mid": "중", "high": "상", "top": "최상"}

HIST_BIN = 100          # 고정 폭. 데이터에서 정하지 않는다(묶음끼리 축을 맞추려고).
MIN_FOR_Z = 3           # 참가자 3명 미만이면 z·백분위를 내지 않는다
MIN_DIFF_N = 20         # 난이도별 정답률은 응답 20건부터


def w(t):
    return sum(2 if unicodedata.east_asian_width(str(c)) in "WF" else 1 for c in str(t))


def pad(t, n, right=False):
    sp = " " * max(0, n - w(t))
    return sp + str(t) if right else str(t) + sp


def anon_label(i):
    """0→A, 25→Z, 26→AA."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return "참가자" + s


def quantile(sorted_vals, q):
    """선형보간 사분위수. n=1이면 그 값."""
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    pos = (n - 1) * q
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=10, help="터미널 표에 몇 명까지")
    ap.add_argument("--anon", action="store_true", help="라벨을 가명으로 (파일에도)")
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args()

    if not RESULTS.exists():
        print(f"❌ {RESULTS}가 없다. 먼저 /teacher-collect를 돌릴 것.")
        return 1
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    records = data.get("records", [])
    if not records:
        print("❌ 레코드가 없다.")
        return 1

    notes = []
    n = len(records)
    scores = [r["score"] for r in records]
    mu = st.mean(scores)
    # 모표준편차(ddof=0) — 이 묶음이 곧 전부지 표본이 아니다.
    sd = st.pstdev(scores) if n > 1 else 0.0
    srt = sorted(scores)

    # ── 참가자별 ────────────────────────────────────────────────
    parts = []
    for i, r in enumerate(records):
        resp = r.get("responses")
        by_diff = None
        mean_elapsed = None
        if isinstance(resp, list) and resp:
            buckets = {d: [0, 0] for d in DIFFICULTY_ORDER}
            for x in resp:
                b = buckets.get(x.get("difficulty"))
                if b:
                    b[1] += 1
                    b[0] += 1 if x.get("correct") else 0
            by_diff = {d: (v[0] / v[1] if v[1] else None) for d, v in buckets.items()}
            el = [x["elapsedMs"] for x in resp if isinstance(x.get("elapsedMs"), int)]
            mean_elapsed = round(st.mean(el)) if el else None

        parts.append({
            "id": r["participantId"],
            "label": r["label"],
            "score": r["score"],
            "correctCount": r["correctCount"],
            "total": r["total"],
            "accuracy": r["accuracy"],
            "z": None, "percentile": None, "rank": None,
            "byCategory": r.get("byCategory", {}),
            "byDifficulty": by_diff,
            "strongest": None, "weakest": None,
            "meanElapsedMs": mean_elapsed,
            "hasResponses": isinstance(resp, list),
        })

    # 석차 — 동점은 같은 등수, 다음은 건너뛴다(1,1,3).
    for i, p in enumerate(sorted(parts, key=lambda x: -x["score"]), 1):
        p["_ord"] = i
    prev, cur = None, 0
    for p in sorted(parts, key=lambda x: -x["score"]):
        if p["score"] != prev:
            cur, prev = p["_ord"], p["score"]
        p["rank"] = cur
    for p in parts:
        del p["_ord"]

    if n >= MIN_FOR_Z:
        for p in parts:
            p["z"] = round((p["score"] - mu) / sd, 3) if sd > 0 else 0.0
            lower = sum(1 for q in parts if q["score"] < p["score"])
            p["percentile"] = round(lower / (n - 1) * 100, 1)
    else:
        notes.append(f"참가자가 {n}명뿐이라 z-점수와 백분위를 내지 않았다(최소 {MIN_FOR_Z}명).")

    # ── 분야별 ──────────────────────────────────────────────────
    cats = []
    for key in CATEGORY_ORDER:
        vals = [(p["byCategory"].get(key), p) for p in parts if key in p["byCategory"]]
        vals = [(v, p) for v, p in vals if isinstance(v, (int, float))]
        if not vals:
            cats.append({"key": key, "label": CATEGORY_LABEL[key], "meanAccuracy": None,
                         "stdev": None, "best": None, "worst": None, "vsOverall": None})
            continue
        nums = [v for v, _ in vals]
        cats.append({
            "key": key, "label": CATEGORY_LABEL[key],
            "meanAccuracy": round(st.mean(nums), 4),
            "stdev": round(st.pstdev(nums), 4) if len(nums) > 1 else 0.0,
            "best": max(vals, key=lambda t: t[0])[1]["id"],
            "worst": min(vals, key=lambda t: t[0])[1]["id"],
            "vsOverall": None,
        })
    overall_cat_mean = st.mean([c["meanAccuracy"] for c in cats if c["meanAccuracy"] is not None])
    for c in cats:
        if c["meanAccuracy"] is not None:
            c["vsOverall"] = round(c["meanAccuracy"] - overall_cat_mean, 4)

    # 강점·약점: 분야 평균 대비 편차가 가장 큰/작은 쪽
    cat_mean = {c["key"]: c["meanAccuracy"] for c in cats}
    for p in parts:
        devs = {k: v - cat_mean[k] for k, v in p["byCategory"].items()
                if isinstance(v, (int, float)) and cat_mean.get(k) is not None}
        if devs:
            p["strongest"] = max(devs, key=devs.get)
            p["weakest"] = min(devs, key=devs.get)

    # ── 난이도별 (전체 집계) — 설계 검증용 ──────────────────────
    buckets = {d: [0, 0] for d in DIFFICULTY_ORDER}
    for r in records:
        if isinstance(r.get("responses"), list):
            for x in r["responses"]:
                b = buckets.get(x.get("difficulty"))
                if b:
                    b[1] += 1
                    b[0] += 1 if x.get("correct") else 0
    diffs = []
    for d in DIFFICULTY_ORDER:
        hit, tot = buckets[d]
        acc = round(hit / tot, 4) if tot >= MIN_DIFF_N else None
        if tot < MIN_DIFF_N:
            notes.append(f"난이도 '{DIFFICULTY_LABEL[d]}'는 응답 {tot}건뿐이라 "
                         f"정답률을 내지 않았다(최소 {MIN_DIFF_N}건).")
        diffs.append({"key": d, "label": DIFFICULTY_LABEL[d],
                      "actualAccuracy": acc, "n": tot, "inverted": False})
    # 쉬운 쪽이 어려운 쪽보다 정답률이 낮으면 라벨이 뒤집힌 것이다.
    known = [d for d in diffs if d["actualAccuracy"] is not None]
    for a, b in zip(known, known[1:]):
        if a["actualAccuracy"] < b["actualAccuracy"]:
            a["inverted"] = b["inverted"] = True

    # ── 전체 ────────────────────────────────────────────────────
    durs = [r["durationMs"] for r in records if isinstance(r.get("durationMs"), int)]
    lo = (min(srt) // HIST_BIN) * HIST_BIN
    hi = (max(srt) // HIST_BIN) * HIST_BIN
    hist = []
    for start in range(lo, hi + HIST_BIN, HIST_BIN):
        hist.append({"from": start, "to": start + HIST_BIN - 1,
                     "count": sum(1 for s in srt if start <= s < start + HIST_BIN)})

    q1, q3 = quantile(srt, .25), quantile(srt, .75)
    overall = {
        "mean": round(mu, 4), "median": st.median(srt), "mode": st.mode(srt),
        "stdev": round(sd, 4), "variance": round(st.pvariance(scores), 4) if n > 1 else 0.0,
        "min": min(srt), "max": max(srt), "range": max(srt) - min(srt),
        "q1": round(q1, 4), "q3": round(q3, 4), "iqr": round(q3 - q1, 4),
        "meanAccuracy": round(st.mean([r["accuracy"] for r in records]), 4),
        "meanDurationMs": round(st.mean(durs)) if durs else None,
        "histogram": hist,
    }

    v2 = sum(1 for r in records if isinstance(r.get("responses"), list))
    if args.anon:
        for i, p in enumerate(sorted(parts, key=lambda x: x["label"])):
            p["label"] = anon_label(i)

    result = {
        "schema": "quiz-analysis/v1",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "anonymized": bool(args.anon),
        "coverage": {
            "participants": n,
            "distinctNames": len({r["name"] for r in records}),
            "v2": v2, "v1": n - v2,
        },
        "overall": overall,
        "participants": [{k: v for k, v in p.items() if k != "hasResponses"} for p in parts],
        "categories": cats,
        "difficulties": diffs,
        "notes": notes,
    }
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 터미널 보고 (파일에 쓴 것과 같은 수를 그대로 쓴다) ──────
    print(f"참가 {n}명 (서로 다른 이름 {result['coverage']['distinctNames']}개) "
          f"· 평균 {mu:.1f} · 표준편차 {sd:.1f} · 중앙값 {overall['median']} "
          f"· 범위 {overall['min']}~{overall['max']}")
    print(f"문항분석 가능 {v2}판 / 총점만 {n - v2}판\n")

    show = sorted(parts, key=lambda x: x["rank"])[:args.top]
    lw = max(w("라벨"), *(w(p["label"]) for p in show))
    print("  " + pad("석차", 4, True) + " " + pad("라벨", lw) + "  " +
          pad("점수", 5, True) + " " + pad("정답률", 7, True) + " " +
          pad("z", 6, True) + " " + pad("백분위", 7, True))
    print("  " + "-" * (4 + lw + 30))
    for p in show:
        print("  " + pad(p["rank"], 4, True) + " " + pad(p["label"], lw) + "  " +
              pad(p["score"], 5, True) + " " +
              pad(f"{p['accuracy']*100:.1f}%", 7, True) + " " +
              pad("—" if p["z"] is None else f"{p['z']:+.2f}", 6, True) + " " +
              pad("—" if p["percentile"] is None else f"{p['percentile']:.0f}", 7, True))

    print("\n분야별 평균 정답률")
    for c in sorted(cats, key=lambda x: (x["meanAccuracy"] is None, x["meanAccuracy"])):
        if c["meanAccuracy"] is None:
            continue
        bar = "█" * round(c["meanAccuracy"] * 24)
        print(f"  {pad(c['label'], 16)} {c['meanAccuracy']*100:5.1f}%  "
              f"{c['vsOverall']:+.3f}  {bar}")
    weakest = min((c for c in cats if c["meanAccuracy"] is not None),
                  key=lambda c: c["meanAccuracy"])
    print(f"  → 가장 약한 분야: {weakest['label']}")

    print("\n난이도별 실제 정답률 (최하→최상으로 내려가야 정상)")
    for d in diffs:
        if d["actualAccuracy"] is None:
            print(f"  {pad(d['label'], 4)} {'—':>7}  (응답 {d['n']}건 — 판정 보류)")
        else:
            mark = "  ⚠️ 역전" if d["inverted"] else ""
            print(f"  {pad(d['label'], 4)} {d['actualAccuracy']*100:6.1f}%  "
                  f"(응답 {d['n']}건){mark}")
    if any(d["inverted"] for d in diffs):
        print("  ⚠️  난이도 라벨이 체감과 어긋난다 — 라벨 재배치를 검토할 것.")

    if notes:
        print("\n판정 보류")
        for m in notes:
            print("  ℹ️  " + m)
    print(f"\n{args.out} 기록 완료.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
