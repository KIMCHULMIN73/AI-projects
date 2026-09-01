#!/usr/bin/env python3
"""[선생님 모드 · 4단계] 참가자 간 비교 + 문항 분석 — 개발 전용.

analysis.json에 comparison / items / itemCoverage / flags를 **덧붙인다.**
3단계가 만든 키(overall/participants/categories/difficulties)는 건드리지 않는다.

시선을 한 번 뒤집어 **사람이 아니라 문항을 채점한다.** 실제 응답만이 알려 줄 수
있는 것이 있다 — 「퀴즈 문제 교차 검증 가이드라인」을 아무리 꼼꼼히 봐도
'아무도 못 맞히는 문항'과 '정답이 둘인 문항'은 사람 눈으로 다 걸러지지 않는다.

    python3 tools/results_compare.py [라벨1 라벨2] [--items] [--min-n N]
"""

import argparse
import json
import math
import re
import statistics as st
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "data" / "results" / "results.json"
ANALYSIS = ROOT / "data" / "results" / "analysis.json"
BANK_DIR = ROOT / "data" / "questions"

CATEGORY_FILES = {
    "korean_history": "korean-history.js", "science": "science.js",
    "world_geography": "world-geography.js", "arts_culture": "arts-culture.js",
}
CATEGORY_LABEL = {"korean_history": "한국사", "science": "과학",
                  "world_geography": "세계지리", "arts_culture": "예술"}
DIFFICULTY_ORDER = ["bottom", "low", "mid", "high", "top"]

MIN_N_P = 12      # 정답률을 말할 수 있는 최소 노출
MIN_N_D = 30      # 변별도를 말할 수 있는 최소 노출
MIN_GROUP = 8     # 상·하위 군이 각각 최소 몇 건이어야 하는가
FAST_MS = 3000    # 문항당 평균 이 미만이면 '속도 이상치'

# ── 플래그의 문턱 ──────────────────────────────────────────────
# **문턱 없이 "오답이 정답보다 많으면 고발"하면 우연을 전부 고발하게 된다.**
# 정답률 40%짜리 문항을 15명이 풀면 정답 6, 오답 3개가 각 3씩인데, 흔들리면
# 그중 하나가 쉽게 6을 넘는다. 실제로 문턱 없이 돌렸더니 '5 vs 4', '8 vs 7'
# 같은 것이 🔴로 쏟아졌다. 그래서 셋 다 표본 크기를 감안한 판정으로 바꿨다.
# 두 번째 함정은 **다중검정**이다. 문항 340개에 α=0.05 검정을 돌리면 아무 문제가
# 없어도 17건쯤이 우연히 걸린다. 실제로 보정 없이 돌렸더니 🔴 3건이 떴는데
# 전부 위양성이었다. 그래서 벤저미니-호크버그(BH)로 위양성 비율을 통제한다.
ALPHA = 0.05      # 유의수준 (BH 보정 후)
TOO_HARD = 0.30   # 찍기(25%) 언저리
TOO_EASY = 0.95
D_BAD = -0.10     # 이보다 더 음수여야 '뒤집힌 문항'으로 본다
D_LOW = 0.20
MIN_WRONG = 15    # '아무도 안 고른 보기'를 말하려면 오답이 이만큼은 나와야 한다


def sign_test(a, b):
    """a가 b보다 크게 나올 확률(단측). 둘을 동전 던지기로 보는 부호검정.

    정답과 최다 오답만 놓고 '둘이 똑같이 매력적이었다면 이만큼 갈릴 확률'을 본다.
    작을수록 우연으로 보기 어렵다.
    """
    n = a + b
    if n == 0:
        return 1.0
    return sum(math.comb(n, k) for k in range(a, n + 1)) / (2 ** n)


def bh_reject(pvals, alpha=ALPHA):
    """벤저미니-호크버그. 통과시킬 인덱스 집합을 돌려준다.

    검정을 m번 돌리면 α=0.05에서도 0.05·m건이 우연히 걸린다. 문항이 수백 개라
    보정 없이는 표가 매번 거짓 고발로 채워진다. BH는 '고발한 것 중 거짓의 비율'을
    α 이하로 눌러 준다 — 본페로니보다 덜 보수적이라 진짜를 덜 놓친다.
    """
    m = len(pvals)
    if m == 0:
        return set()
    order = sorted(range(m), key=lambda i: pvals[i])
    cut = -1
    for rank, i in enumerate(order, 1):
        if pvals[i] <= rank / m * alpha:
            cut = rank
    return set(order[:cut]) if cut > 0 else set()


def norm_cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def d_test(hit_u, hit_l, k):
    """변별도가 0보다 작다고 말할 수 있는가 — 두 비율 차의 단측 검정.

    D는 각 k건짜리 두 군의 정답률 차라서 k가 작으면 엄청나게 흔들린다.
    k=8이면 D의 표준오차가 0.24쯤이라 **D=-0.2도 우연의 범위 안**이다.
    실제로 문턱만 두고 돌렸더니 심어 둔 결함이 하나도 없는 데이터에서
    negative-D가 5~13건씩 떴다. 그래서 D도 검정으로 바꾸고 BH로 보정한다.
    돌려주는 것은 (D, 표준오차, 단측 p값).
    """
    d = hit_u / k - hit_l / k
    pool = (hit_u + hit_l) / (2 * k)
    se = math.sqrt(2 * pool * (1 - pool) / k) if 0 < pool < 1 else 0.0
    if se == 0:
        return d, 0.0, (0.0 if d < 0 else 1.0)
    return d, se, norm_cdf(d / se)


def wilson(hit, n, z=1.96):
    """윌슨 신뢰구간. 표본이 작을 때 단순 비율보다 정직하다."""
    if n == 0:
        return (0.0, 1.0)
    ph = hit / n
    d = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / d
    m = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - m), min(1.0, c + m))

# 문항 텍스트만 긁어 온다. JS 런타임이 없어 import할 수 없다(validate_bank.py와 같은 방식).
OBJECT_RE = re.compile(r"\{\s*\n\s*id:\s*'([^']+)',(.*?)\n  \},", re.S)
QUESTION_RE = re.compile(r"\n\s*question:\s*(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')")


def w(t):
    return sum(2 if unicodedata.east_asian_width(str(c)) in "WF" else 1 for c in str(t))


def pad(t, n, right=False):
    sp = " " * max(0, n - w(t))
    return sp + str(t) if right else str(t) + sp


def load_question_text():
    out = {}
    for filename in CATEGORY_FILES.values():
        src = (BANK_DIR / filename).read_text(encoding="utf-8")
        for m in OBJECT_RE.finditer(src):
            q = QUESTION_RE.search(m.group(2))
            out[m.group(1)] = q.group(1)[1:-1] if q else ""
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("labels", nargs="*", help="1:1 비교할 라벨 둘")
    ap.add_argument("--items", action="store_true", help="문항 분석까지")
    ap.add_argument("--min-n", type=int, default=MIN_N_P)
    args = ap.parse_args()

    if not ANALYSIS.exists():
        print("❌ analysis.json이 없다. 먼저 /teacher-stats를 돌릴 것.")
        return 1
    a = json.loads(ANALYSIS.read_text(encoding="utf-8"))
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    records = {r["participantId"]: r for r in data["records"]}
    parts = a["participants"]
    by_id = {p["id"]: p for p in parts}
    cat_mean = {c["key"]: c["meanAccuracy"] for c in a["categories"]}

    # ── (A) 분야별 강약 프로파일 — 원값이 아니라 전체 평균 대비 편차 ──
    profiles = []
    for p in parts:
        profiles.append({"id": p["id"], "vsOverall": {
            k: round(v - cat_mean[k], 4)
            for k, v in p["byCategory"].items()
            if isinstance(v, (int, float)) and cat_mean.get(k) is not None}})

    # ── (B) 이상치 — 알리기만 하고 판정하지 않는다 ──────────────
    o = a["overall"]
    lo_fence, hi_fence = o["q1"] - 1.5 * o["iqr"], o["q3"] + 1.5 * o["iqr"]
    outliers = {
        "high": [p["id"] for p in parts
                 if p["score"] > hi_fence or (p["z"] is not None and p["z"] >= 2)],
        "low": [p["id"] for p in parts
                if p["score"] < lo_fence or (p["z"] is not None and p["z"] <= -2)],
        "fast": [{"id": p["id"], "meanElapsedMs": p["meanElapsedMs"]} for p in parts
                 if p["meanElapsedMs"] is not None and p["meanElapsedMs"] < FAST_MS],
        "mismatch": [],
    }
    # 정답률은 높은데 점수가 낮은 경우 — 쉬운 문제만 맞힌 것이다.
    acc_rank = {p["id"]: i for i, p in
                enumerate(sorted(parts, key=lambda x: -x["accuracy"]), 1)}
    for p in parts:
        gap = p["rank"] - acc_rank[p["id"]]
        if gap >= max(3, len(parts) // 4):
            outliers["mismatch"].append(
                {"id": p["id"], "accuracyRank": acc_rank[p["id"]], "scoreRank": p["rank"]})

    head2head = None
    if len(args.labels) == 2:
        pick = [next((p for p in parts if p["label"] == lb), None) for lb in args.labels]
        if all(pick):
            head2head = {"labels": args.labels, "ids": [p["id"] for p in pick]}
        else:
            print(f"⚠️  라벨을 찾지 못했다: "
                  f"{[lb for lb, p in zip(args.labels, pick) if p is None]}")

    a["comparison"] = {"profiles": profiles, "outliers": outliers, "head2head": head2head}

    # ── (C) 문항 분석 ───────────────────────────────────────────
    items, flags, suspects, d_cands = [], [], [], []
    v2 = [r for r in data["records"] if isinstance(r.get("responses"), list)]
    seen_ids = set()
    if args.items and v2:
        text = load_question_text()
        agg = defaultdict(lambda: {"n": 0, "hit": 0, "choices": [0, 0, 0, 0],
                                   "answerIndex": None, "category": None,
                                   "difficulty": None, "scores": []})
        for r in v2:
            score = r["score"]
            for x in r["responses"]:
                e = agg[x["id"]]
                e["n"] += 1
                e["hit"] += 1 if x.get("correct") else 0
                si = x.get("selectedIndex")
                if isinstance(si, int) and 0 <= si < 4:
                    e["choices"][si] += 1
                e["answerIndex"] = x.get("answerIndex")
                e["category"] = x.get("category")
                e["difficulty"] = x.get("difficulty")
                e["scores"].append((score, bool(x.get("correct"))))
        seen_ids = set(agg)

        for qid, e in sorted(agg.items()):
            p_val = round(e["hit"] / e["n"], 4) if e["n"] >= args.min_n else None

            # 변별도 — 그 문항을 본 판만 모아 **그 판의 점수**로 상하위 27%를 가른다.
            # 판마다 시험지가 달라 점수가 엄밀히 같은 잣대가 아니므로 근사다.
            d_val, method, d_se, d_pv = None, None, None, None
            if e["n"] >= MIN_N_D:
                ordered = sorted(e["scores"], key=lambda t: -t[0])
                k = max(MIN_GROUP, round(len(ordered) * 0.27))
                if k * 2 <= len(ordered):
                    up = [c for _, c in ordered[:k]]
                    dn = [c for _, c in ordered[-k:]]
                    raw_d, d_se, d_pv = d_test(sum(up), sum(dn), k)
                    d_val = round(raw_d, 4)
                    method = "play-score-27pct"

            # 플래그 판정은 루프 밖에서 한다 — 다중검정 보정에 전체 p값이 필요하다.
            fl = []
            lo_ci, hi_ci = wilson(e["hit"], e["n"]) if e["n"] else (0.0, 1.0)
            susp = None
            if p_val is not None and isinstance(e["answerIndex"], int):
                ai = e["answerIndex"]
                worst = max((i for i in range(4) if i != ai), key=lambda i: e["choices"][i])
                if e["choices"][worst] > e["choices"][ai]:
                    susp = (worst, sign_test(e["choices"][worst], e["choices"][ai]))
            if p_val is not None and e["n"] >= MIN_N_D:
                # 표본이 작으면 구간이 넓어 판정할 수 없다. n 문턱을 함께 건다.
                if hi_ci < TOO_HARD:
                    fl.append("too-hard")
                    flags.append({"level": "warn", "code": "too-hard", "id": qid,
                                  "note": f"정답률 {p_val*100:.0f}% (95% 상한 {hi_ci*100:.0f}%, "
                                          f"n={e['n']}) — 찍기(25%) 수준"})
                if lo_ci > TOO_EASY:
                    fl.append("too-easy")
                    flags.append({"level": "warn", "code": "too-easy", "id": qid,
                                  "note": f"정답률 {p_val*100:.0f}% (95% 하한 {lo_ci*100:.0f}%, "
                                          f"n={e['n']})"})
            if d_val is not None and d_val < 0:
                d_cands.append((qid, d_val, d_pv, e["n"]))
            elif d_val is not None and d_val + 1.96 * d_se < D_LOW:
                # 구간 전체가 문턱 아래일 때만. 점추정만 보면 잡음을 고발하게 된다.
                fl.append("low-D")
                flags.append({"level": "warn", "code": "low-D", "id": qid,
                              "note": f"D={d_val}±{1.96*d_se:.2f} (n={e['n']}) "
                                      f"— 변별을 못 한다"})
            # **오답 수를 기준으로 본다.** 전체 노출로 걸면 최하 난이도 문항이
            # 통째로 걸린다 — 92%가 맞히는 문항은 오답이 3건뿐이라 어떤 보기가
            # 0인 게 당연하다. 실제로 n 기준으로 걸었더니 46건이 떴고 전부
            # 최하 문항이었다.
            wrong_total = e["n"] - e["hit"]
            if wrong_total >= MIN_WRONG and 0 in e["choices"]:
                fl.append("dead-distractor")
                flags.append({"level": "warn", "code": "dead-distractor", "id": qid,
                              "note": f"아무도 고르지 않은 보기가 있다 "
                                      f"(오답 {wrong_total}건 중) — 실질 3지선다"})
            suspects.append((qid, susp))

            items.append({
                "id": qid, "question": text.get(qid, ""), "category": e["category"],
                "difficulty": e["difficulty"], "n": e["n"], "p": p_val, "d": d_val,
                "method": method, "choiceCounts": e["choices"],
                "answerIndex": e["answerIndex"], "flags": fl,
            })

    # ── answer-suspect: 전체 p값을 모아 BH로 보정한 뒤에만 고발한다 ──
    cand = [(qid, sp) for qid, sp in suspects if sp is not None]
    if cand:
        keep = bh_reject([sp[1] for _, sp in cand])
        for idx, (qid, (worst, pv)) in enumerate(cand):
            if idx not in keep:
                continue
            it = next(i for i in items if i["id"] == qid)
            it["flags"].append("answer-suspect")
            flags.append({"level": "red", "code": "answer-suspect", "id": qid,
                          "note": f"보기{worst + 1}이 정답보다 많이 선택됨 "
                                  f"({it['choiceCounts'][worst]} vs "
                                  f"{it['choiceCounts'][it['answerIndex']]}, "
                                  f"부호검정 p={pv:.5f}, BH 보정 통과)"})
    # ── negative-D도 같은 방식으로 보정한다 ──
    if d_cands:
        keep = bh_reject([pv for _, _, pv, _ in d_cands])
        for idx, (qid, dv, pv, nn) in enumerate(d_cands):
            if idx not in keep:
                continue
            next(i for i in items if i["id"] == qid)["flags"].append("negative-D")
            flags.append({"level": "red", "code": "negative-D", "id": qid,
                          "note": f"D={dv} (n={nn}, 단측 p={pv:.5f}, BH 보정 통과) "
                                  f"— 잘 본 판일수록 틀린다"})
    a["itemTests"] = {"answerSuspectTested": len(cand), "dTested": len(d_cands),
                      "method": "benjamini-hochberg", "alpha": ALPHA}

    a["items"] = items
    a["itemCoverage"] = {
        "seen": len(seen_ids),
        "judged": sum(1 for i in items if i["p"] is not None),
        "minN": args.min_n,
    }
    a["flags"] = flags
    ANALYSIS.write_text(json.dumps(a, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 보고 — 판정 가능 범위를 가장 먼저 ───────────────────────
    cov = a["itemCoverage"]
    print(f"출제된 문항 {cov['seen']}개 중 판정 가능 {cov['judged']}개 "
          f"(최소 노출 {cov['minN']}회)")
    if v2 and cov["judged"] == 0:
        exp = len(v2) * 40 / 410
        print(f"  → 참가 {len(v2)}판이면 문항당 평균 노출이 {exp:.1f}회다. "
              f"대부분 판정 보류가 정상이고, 결함이 아니라 데이터가 모자란 것이다.")
    if not v2:
        print("  → v2 기록이 없어 문항 분석을 통째로 건너뛰었다.")

    print("\n분야별 강약 — 전체 평균 대비 편차 (상위 8명)")
    keys = [c["key"] for c in a["categories"]]
    lw = max(w("라벨"), *(w(p["label"]) for p in parts))
    print("  " + pad("라벨", lw) + "".join(" " + pad(CATEGORY_LABEL[k], 8, True) for k in keys))
    prof = {p["id"]: p for p in profiles}
    for p in sorted(parts, key=lambda x: x["rank"])[:8]:
        row = "  " + pad(p["label"], lw)
        for k in keys:
            v = prof[p["id"]]["vsOverall"].get(k)
            row += " " + pad("—" if v is None else f"{v:+.2f}", 8, True)
        print(row)

    print("\n이상치")
    lab = lambda i: by_id[i]["label"]
    print(f"  높음: {', '.join(lab(i) for i in outliers['high']) or '없음'}")
    print(f"  낮음: {', '.join(lab(i) for i in outliers['low']) or '없음'}")
    print(f"  빠름(문항당 {FAST_MS/1000:.0f}초 미만): "
          f"{', '.join(f'{lab(x[chr(105)+chr(100)])}' for x in outliers['fast']) or '없음'}")
    if outliers["mismatch"]:
        print("  정답률-점수 불일치 (쉬운 문제만 맞힌 쪽):")
        for m in outliers["mismatch"]:
            print(f"    {lab(m['id'])}: 정답률 {m['accuracyRank']}위인데 점수 {m['scoreRank']}위")
    else:
        print("  정답률-점수 불일치: 없음")

    red = [f for f in flags if f["level"] == "red"]
    print(f"\n재검토 후보 — 🔴 {len(red)}건 / ⚠️ {len(flags) - len(red)}건")
    for f in sorted(flags, key=lambda x: x["level"] != "red")[:15]:
        it = next(i for i in items if i["id"] == f["id"])
        mark = "🔴" if f["level"] == "red" else "⚠️ "
        print(f"  {mark} {f['id']} (n={it['n']}) {f['note']}")
        print(f"      {it['question'][:60]}")
    if red:
        cats = {items and next(i for i in items if i['id'] == f['id'])['category']
                for f in red}
        print("\n  다음:")
        for c in sorted(x for x in cats if x):
            print(f"    /quiz-validate {CATEGORY_LABEL[c]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
