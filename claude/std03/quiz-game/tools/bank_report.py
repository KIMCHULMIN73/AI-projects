#!/usr/bin/env python3
"""문제 은행 현황과 '어디에 문항을 더 넣어야 하는지'를 계산한다.

  python3 tools/bank_report.py            # 개수 · 난이도 · 정답 위치 현황
  python3 tools/bank_report.py --targets  # 부족한 (분야, 난이도) 순위
  python3 tools/bank_report.py --json     # 위 둘을 기계 판독용으로

핵심 지표는 **여유 배수 = 은행 문항 수 ÷ 한 판 할당량**이다.
한 판은 분야마다 top1/high2/mid4/low2/bottom1을 뽑으므로, 이 배수가 작은 자리일수록
다시 도전했을 때 같은 문제를 또 만날 확률이 높다. 그 자리가 곧 '부족한 곳'이다.

[개발용] 앱은 이 파일을 로드하지 않는다.
"""
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

BANK_DIR = Path(__file__).resolve().parent.parent / "data" / "questions"

STEMS = ["korean-history", "science", "world-geography", "arts-culture"]
NAME = {"korean-history": "한국사", "science": "과학",
        "world-geography": "세계지리", "arts-culture": "예술·문화"}
PREFIX = {"korean-history": "KH", "science": "SC",
          "world-geography": "WG", "arts-culture": "AC"}
# storage.js의 DIFFICULTY_QUOTA와 같아야 한다. 여기서 갈라지면 여유 배수가 거짓말을 한다.
QUOTA = {"bottom": 1, "low": 2, "mid": 4, "high": 2, "top": 1}
DIFS = [("bottom", "최하", 10), ("low", "하", 20), ("mid", "중", 40),
        ("high", "상", 20), ("top", "최상", 10)]
LABEL = {c: l for c, l, _ in DIFS}
OBJ = re.compile(r"  \{\n    id: '([^']+)',\n(.*?)\n  \},", re.S)


def w(t):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(t))


def pad(t, n, right=False):
    sp = " " * max(0, n - w(t))
    return sp + str(t) if right else str(t) + sp


def load(stem):
    src = (BANK_DIR / f"{stem}.js").read_text(encoding="utf-8")
    return [(m.group(1),
             re.search(r"difficulty: '(\w+)'", m.group(2)).group(1),
             int(re.search(r"answerIndex: (\d)", m.group(2)).group(1)))
            for m in OBJ.finditer(src)]


def chi2(counts, n):
    e = n / 4
    return sum((counts.get(i, 0) - e) ** 2 / e for i in range(4)) if e else 0.0


def bias_note(counts, n):
    x = chi2(counts, n)
    if n < 20:
        return x, "표본 부족"
    if x > 11.345:
        return x, "🔴 편향"
    if x > 7.815:
        return x, "⚠️ 편향 의심"
    return x, "✅ 고름"


def collect():
    data = {s: load(s) for s in STEMS}
    rows = []
    for s in STEMS:
        counts = Counter(d for _, d, _ in data[s])
        for code, _, base in DIFS:
            n = counts.get(code, 0)
            # 이 자리에 넣으면 뒤쪽 문항 id가 몇 개나 밀리는가.
            # 파일이 bottom→top 순이라 쉬운 난이도일수록 비싸다.
            after = sum(counts.get(c2, 0) for c2, _, _ in DIFS[DIFS.index((code, LABEL[code], base)) + 1:])
            rows.append({
                "stem": s, "category": NAME[s], "difficulty": code,
                "label": LABEL[code], "count": n,
                "quota": QUOTA[code],
                "slack": n / QUOTA[code],           # 여유 배수
                "meets_quota": n >= QUOTA[code],
                "design_share": base,
                "share": n / len(data[s]) * 100 if data[s] else 0.0,
                "shift": after,                     # 밀리는 id 수
            })
    return data, rows


def rank(rows, data):
    """부족한 자리 순위. 앞에 올수록 먼저 채워야 한다."""
    totals = {s: len(v) for s, v in data.items()}
    biggest = max(totals.values())

    def key(r):
        return (
            0 if not r["meets_quota"] else 1,      # 할당량 미달이 최우선
            r["slack"],                             # 여유 배수가 작을수록 앞
            -(biggest - totals[r["stem"]]),         # 총계가 작은 분야를 먼저
            r["design_share"] - r["share"],         # 설계 비율 대비 모자란 쪽
        )
    return sorted(rows, key=key)


def main():
    data, rows = collect()
    totals = {s: len(v) for s, v in data.items()}
    total = sum(totals.values())
    ranked = rank(rows, data)
    want_json = "--json" in sys.argv
    only_targets = "--targets" in sys.argv

    if want_json:
        print(json.dumps({
            "total": total,
            "by_category": {NAME[s]: totals[s] for s in STEMS},
            "rows": rows,
            "targets": ranked[:8],
        }, ensure_ascii=False, indent=2))
        return

    if not only_targets:
        print(f"── 문제 은행 현황 ({total}문항) ──\n")
        print(pad("분야", 12) + pad("총계", 6, 1)
              + "".join(pad(l, 7, 1) for _, l, _ in DIFS)
              + pad("정답 0/1/2/3", 16, 1) + pad("χ²", 8, 1) + "  편향")
        for s in STEMS:
            c = Counter(d for _, d, _ in data[s])
            a = Counter(i for _, _, i in data[s])
            x, note = bias_note(a, totals[s])
            print(pad(NAME[s], 12) + pad(totals[s], 6, 1)
                  + "".join(pad(c.get(k, 0), 7, 1) for k, _, _ in DIFS)
                  + pad("/".join(str(a.get(i, 0)) for i in range(4)), 16, 1)
                  + pad(f"{x:.2f}", 8, 1) + "  " + note)
        agg = Counter()
        agga = Counter()
        for s in STEMS:
            agg.update(d for _, d, _ in data[s])
            agga.update(i for _, _, i in data[s])
        x, note = bias_note(agga, total)
        print(pad("합계", 12) + pad(total, 6, 1)
              + "".join(pad(agg.get(k, 0), 7, 1) for k, _, _ in DIFS)
              + pad("/".join(str(agga.get(i, 0)) for i in range(4)), 16, 1)
              + pad(f"{x:.2f}", 8, 1) + "  " + note)
        print()

    print("── 부족한 자리 (여유 배수 = 은행 문항 ÷ 한 판 할당량) ──\n")
    print(pad("순위", 6) + pad("분야", 12) + pad("난이도", 8)
          + pad("문항", 6, 1) + pad("할당", 6, 1) + pad("여유배수", 10, 1)
          + pad("밀림", 7, 1) + "  비고")
    blocking = [r for r in ranked if not r["meets_quota"]]
    for i, r in enumerate(ranked[:8], 1):
        note = ""
        if not r["meets_quota"]:
            note = "🔴 할당량 미달 — 샘플링이 인접 난이도로 보충한다"
        elif r["shift"] >= 60:
            note = "id 재부여 비용 큼 — 문서 참조를 함께 고쳐야 한다"
        elif r["slack"] <= 10:
            note = "가장 자주 반복되는 자리"
        print(pad(i, 6) + pad(r["category"], 12) + pad(r["label"], 8)
              + pad(r["count"], 6, 1) + pad(r["quota"], 6, 1)
              + pad(f"{r['slack']:.1f}배", 10, 1)
              + pad(r["shift"], 7, 1) + "  " + note)

    print()
    if blocking:
        print(f"🔴 할당량 미달 {len(blocking)}자리 — 여기부터 채워야 한다.")
        sys.exit(1)
    lo = ranked[0]
    print(f"권장: {lo['category']} / {lo['label']}  "
          f"(현재 {lo['count']}문항, 여유 {lo['slack']:.1f}배, id {lo['shift']}개 밀림)")
    cheap = min(ranked[:8], key=lambda r: r["shift"])
    if cheap["shift"] < lo["shift"] and lo["shift"] >= 60:
        print(f"      id를 덜 밀고 싶다면: {cheap['category']} / {cheap['label']} "
              f"({cheap['shift']}개만 밀린다)")


if __name__ == "__main__":
    main()
