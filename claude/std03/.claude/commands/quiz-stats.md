---
description: 분야(없으면 전체) 문항 DB의 개수·난이도 분포·정답 위치 편향을 한눈에 본다
argument-hint: "[분야 — 한국사 | 과학 | 세계지리 | 예술 (비우면 전체)]"
allowed-tools: Bash, Read, Grep
---

통계 대상: **$ARGUMENTS**

비어 있으면 은행 전체를, 분야를 주면 그 분야만 본다. 여러 분야를 나열해도 된다.

> 이 하네스는 위치 인수(`$1`)를 신뢰할 수 없다 — 0-based로 밀려 들어온다.
> **`$ARGUMENTS` 한 덩어리만 쓰고 분야는 그 문자열에서 골라낼 것.**

보는 것은 셋이다.

1. **개수** — 분야별 총 문항 수. 분야마다 다를 수 있다(`/quiz-add`로 늘어난다)
2. **난이도 분포** — 최하/하/중/상/최하가 설계 비율 10:20:40:20:10에서 얼마나 벗어났는지
3. **정답 위치 편향** — `answerIndex`가 0~3에 고르게 퍼졌는지. **χ² 검정으로 판정한다**

## 세 자매 명령어의 역할 분담

같은 은행을 보지만 보는 각도가 다르다. 헷갈리면 아래로 고른다.

| 명령 | 범위 | 무엇을 보나 |
|---|---|---|
| `/quiz-stats` | 분야 전체 / 은행 전체 | **분포가 통계적으로 치우쳤나** — 숫자만 본다 |
| `/quiz-range` | 번호 구간 | 방금 손댄 구간이 치우쳤나 — 문항 목록까지 본다 |
| `/quiz-validate` | 분야 / 문항 | **내용**이 모호한가 — 텍스트를 읽는다 |

`/quiz-stats`는 **문항 내용을 전혀 보지 않는다.** 정답이 맞는지, 표현이 모호한지는 `/quiz-validate`의 몫이다.

## 인수

| 형태 | 예 | 뜻 |
|---|---|---|
| 비움 | `/quiz-stats` | 은행 전체 + 분야별 비교 |
| 분야 하나 | `/quiz-stats 과학` | 과학만 |
| 분야 여럿 | `/quiz-stats 한국사 과학` | 둘을 나란히 |

표기는 다른 명령어와 같다: `한국사`·`KH` / `과학`·`SC` / `세계지리`·`지리`·`WG` / `예술`·`문화`·`AC`.
인식하지 못하는 값이면 스크립트가 알려 주니, 그때는 되묻고 멈출 것.

## 1. 센다

```bash
cd quiz-game && python3 - "$ARGUMENTS" <<'PY'
import re, sys, unicodedata
from collections import Counter

def w(t):
    return sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in str(t))
def pad(t, n, right=False):
    sp = ' ' * max(0, n - w(t))
    return sp + str(t) if right else str(t) + sp

ALIAS = {'한국사':'korean-history','kh':'korean-history','korean-history':'korean-history',
         'korean_history':'korean-history',
         '과학':'science','sc':'science','science':'science',
         '세계지리':'world-geography','지리':'world-geography','wg':'world-geography',
         'world-geography':'world-geography','world_geography':'world-geography',
         '예술':'arts-culture','문화':'arts-culture','시사':'arts-culture','ac':'arts-culture',
         'arts-culture':'arts-culture','arts_culture':'arts-culture'}
NAME = {'korean-history':'한국사','science':'과학','world-geography':'세계지리','arts-culture':'예술·문화'}
PRE  = {'korean-history':'KH','science':'SC','world-geography':'WG','arts-culture':'AC'}
ALL  = ['korean-history','science','world-geography','arts-culture']
DIFS = [('bottom','최하',10),('low','하',20),('mid','중',40),('high','상',20),('top','최상',10)]
OBJ  = re.compile(r"  \{\n    id: '([^']+)',\n(.*?)\n  \},", re.S)

def load(stem):
    src = open(f'data/questions/{stem}.js', encoding='utf-8').read()
    return [(m.group(1),
             re.search(r"difficulty: '(\w+)'", m.group(2)).group(1),
             int(re.search(r"answerIndex: (\d)", m.group(2)).group(1)))
            for m in OBJ.finditer(src)]

def chi2(counts, n):
    e = n / 4
    return sum((counts.get(i, 0) - e) ** 2 / e for i in range(4)) if e else 0.0

def verdict(c, n):
    x = chi2(c, n)
    top = max(range(4), key=lambda i: c.get(i, 0))
    pct = c.get(top, 0) / n * 100 if n else 0
    if n < 20:   return x, f"표본 {n}개 — 판정 보류"
    if x > 11.345: return x, f"🔴 편향 — {top}번에 {pct:.0f}% (χ²>11.34, p<0.01)"
    if x > 7.815:  return x, f"⚠️ 편향 의심 — {top}번이 {pct:.0f}% (χ²>7.81, p<0.05)"
    return x, "✅ 고름"

def streak(rows):
    """정답 위치가 같은 문항이 가장 길게 이어진 구간. 비율이 고르더라도
    한자리에 몰려 있으면 플레이 중에 눈치채기 쉬우므로 따로 본다."""
    best = cur = 1
    at = start = rows[0][0]
    for i in range(1, len(rows)):
        if rows[i][2] == rows[i - 1][2]:
            cur += 1
        else:
            cur, start = 1, rows[i][0]
        if cur > best:
            best, at = cur, start
    return best, at

args  = sys.argv[1].split() if len(sys.argv) > 1 else []
stems, unknown = [], []
for a in args:
    (stems if ALIAS.get(a.lower()) else unknown).append(ALIAS.get(a.lower(), a))
if unknown:
    print(f"분야를 알 수 없습니다: {', '.join(repr(u) for u in unknown)}")
    print("받는 값: 한국사 | 과학 | 세계지리 | 예술  (비우면 전체)")
    sys.exit(2)
stems = stems or ALL
scope = "전체" if stems == ALL else " + ".join(NAME[s] for s in stems)

data = {s: load(s) for s in stems}
total = sum(len(v) for v in data.values())
print(f"── 문제 은행 통계 · {scope} ({total}문항) ──\n")

# ── 1. 개수 + 난이도 ──────────────────────────────────────────
hdr = pad('분야', 12) + pad('총계', 6, 1) + ''.join(pad(l, 7, 1) for _, l, _ in DIFS)
print("── 개수 · 난이도 " + "─" * 44)
print(hdr)
agg = Counter()
for s in stems:
    c = Counter(d for _, d, _ in data[s]); agg.update(c)
    print(pad(NAME[s], 12) + pad(len(data[s]), 6, 1)
          + ''.join(pad(c.get(k, 0), 7, 1) for k, _, _ in DIFS))
if len(stems) > 1:
    print(pad('합계', 12) + pad(total, 6, 1)
          + ''.join(pad(agg.get(k, 0), 7, 1) for k, _, _ in DIFS))

print("\n" + pad('난이도', 8) + pad('개수', 6, 1) + pad('비율', 9, 1)
      + pad('기준', 8, 1) + pad('차이', 9, 1) + '    분포')
for code, label, base in DIFS:
    n = agg.get(code, 0); got = n / total * 100 if total else 0
    d = got - base
    mark = '  ' if abs(d) <= 3 else (' !' if abs(d) <= 8 else '!!')
    print(pad(label, 8) + f"{n:>6}{got:>8.1f}%{base:>7}%{d:>+8.1f}%{mark} {'█' * round(got / 2)}")
extra = set(agg) - {c for c, _, _ in DIFS}
if extra: print(f"  ⚠ 알 수 없는 난이도 값: {', '.join(sorted(extra))}")

# ── 2. 정답 위치 편향 ─────────────────────────────────────────
print("\n── 정답 위치 편향 " + "─" * 43)
print(pad('분야', 12) + ''.join(pad(f'{i}번', 6, 1) for i in range(4))
      + pad('최대', 8, 1) + pad('χ²', 8, 1) + '  판정')
allc = Counter()
for s in stems:
    c = Counter(a for _, _, a in data[s]); allc.update(c); n = len(data[s])
    x, v = verdict(c, n)
    print(pad(NAME[s], 12) + ''.join(pad(c.get(i, 0), 6, 1) for i in range(4))
          + pad(f"{max(c.values())/n*100:.0f}%", 8, 1) + pad(f"{x:.2f}", 8, 1) + '  ' + v)
if len(stems) > 1:
    x, v = verdict(allc, total)
    print(pad('합계', 12) + ''.join(pad(allc.get(i, 0), 6, 1) for i in range(4))
          + pad(f"{max(allc.values())/total*100:.0f}%", 8, 1) + pad(f"{x:.2f}", 8, 1) + '  ' + v)

print("\n" + pad('위치', 8) + pad('개수', 6, 1) + pad('비율', 9, 1)
      + pad('기준', 8, 1) + pad('차이', 9, 1) + '    분포')
for i in range(4):
    n = allc.get(i, 0); got = n / total * 100 if total else 0
    d = got - 25
    mark = '  ' if abs(d) <= 3 else (' !' if abs(d) <= 8 else '!!')
    print(pad(f'{i}번', 8) + f"{n:>6}{got:>8.1f}%{25:>7}%{d:>+8.1f}%{mark} {'█' * round(got / 2)}")

# ── 3. 연속 쏠림 ──────────────────────────────────────────────
print("\n── 같은 정답 위치가 연달아 이어진 구간 " + "─" * 22)
worst = []
for s in stems:
    b, at = streak(data[s])
    worst.append((b, s, at))
    print(f"  {NAME[s]:8} 최장 {b}연속  ({at}부터)" if len(stems) > 1 else
          f"  최장 {b}연속  ({at}부터)")
m = max(worst)[0]
print(f"\n  {'✅ 4연속 이하 — 플레이 중 눈치채기 어렵다' if m <= 4 else '⚠️ ' + str(m) + '연속 — 한 판에 몰려 나오면 눈에 띈다'}")
PY
```

## 2. 읽는 법

### 개수

분야마다 달라도 **결함이 아니다.** `/quiz-add`로 한 분야만 늘리면 그렇게 된다. 게임은 은행 크기와 무관하게 늘 40문제를 내고, 늘어난 만큼 재도전 시 덜 겹칠 뿐이다.

한 판이 성립하려면 **분야마다 `최상1 / 상2 / 중4 / 하2 / 최하1`**만 있으면 된다. 지금은 어느 난이도든 그 열 배 안팎이라 한참 여유가 있다.

### 난이도 분포

`기준` 칸은 설계 당시의 비율(10:20:40:20:10)이다. **차이가 났다고 곧바로 고칠 일이 아니다.**

| 상황 | 뜻 |
|---|---|
| 의도적으로 한 난이도를 늘렸다 | 정상. `validate_bank.py`의 `EXPECTED_BANK_MIX`가 이미 갱신돼 있어야 한다 |
| 늘린 적 없는데 어긋났다 | 라벨이 잘못 붙었을 수 있다. `validate_bank.py`를 돌려 볼 것 |
| 어떤 난이도가 할당량 미만 | **이때만 실제 문제다.** 샘플링이 인접 난이도 보충 경로를 타서 비율이 흔들린다 |

한 난이도만 크게 늘리면 그 난이도의 문항이 **상대적으로 덜 자주 뽑힌다**(할당량은 그대로인데 후보만 늘어서). 변화를 원해서 늘렸다면 의도한 결과다.

### 정답 위치 편향 — χ²를 쓰는 이유

눈으로 "0번이 좀 많네"를 판정하면 표본 크기를 무시하게 된다. 10문항 중 4개가 0번인 것과 400문항 중 160개가 0번인 것은 전혀 다른 이야기인데, 비율은 똑같이 40%다.

χ² 검정은 그 차이를 셈에 넣는다. 자유도 3에서:

| χ² | 판정 | 뜻 |
|---|---|---|
| 7.81 미만 | ✅ 고름 | 우연으로 설명되는 범위 |
| 7.81 ~ 11.34 | ⚠️ 편향 의심 | p<0.05. 문항을 더 넣기 전에 손보는 편이 낫다 |
| 11.34 초과 | 🔴 편향 | p<0.01. **찍기가 통한다. 반드시 고칠 것** |

20문항 미만이면 판정을 보류한다 — 그 크기에서는 어떤 분포든 우연으로 설명된다.

> 이 프로젝트는 여기서 크게 데인 적이 있다. 400문항으로 확장했을 때 **317개의 정답이 0번**이라 첫 보기만 찍어도 79점이 나왔다(CLAUDE.md 사후 수정 이력 §7). `validate_bank.py`는 이 검사를 하지 않으므로 이 명령이 유일한 그물이다.

### 연속 쏠림

비율이 완벽해도 **같은 번호가 연달아 나오면** 플레이 중에 눈치챈다. 출제 순서는 매 판 섞이므로 파일 순서의 연속이 그대로 화면에 나오지는 않지만, 같은 분야·같은 난이도끼리는 함께 뽑힐 수 있어 무의미한 지표가 아니다.

5연속 이상이면 그 구간의 보기 순서를 손볼 만하다.

## 3. 편향이 나왔을 때

**보기 순서를 바꾸고 `answerIndex`를 다시 계산한다.** 두 가지를 조심할 것.

- **네 보기가 모두 숫자면(연도·개수·수치) 섞지 말고 오름차순을 유지한다.** 기계적으로 섞었다가 `1945년, 1910년, 1929년, 1919년`이 되어 29문항을 되돌린 적이 있다.
- 보기를 건드렸으면 **같은 것을 가리키는 보기가 생기지 않았는지** 확인한다. `이벌찬`과 `각간`처럼 이름만 다른 동일물은 `validate_bank.py`의 중복 검사(문자열 비교)를 통과해 버린다.

어느 문항을 고칠지는 `/quiz-range`로 구간을 좁혀 고른다. 고친 뒤 이 명령을 다시 돌리고 은행 전체도 확인한다.

```bash
cd quiz-game && python3 tools/validate_bank.py
```

## 보고 형식

```
과학 110문항 — 최하10 하20 중40 상30 최상10

난이도  상이 27.3%로 기준(20%)보다 높다. /quiz-add로 10문항 늘린 결과라 의도된 것.
편향    0번 32 / 1번 27 / 2번 28 / 3번 23,  χ²=1.49  ✅ 고름
연속    최장 4연속 (SC-066부터) — 손볼 수준은 아니다
```

- **판정을 먼저, 숫자는 근거로** 낸다. 표를 그대로 옮겨 붙이지 말 것.
- 기준에서 벗어난 항목은 **그것이 의도된 것인지 아닌지**까지 말한다. 판단이 서지 않으면 사용자에게 묻는다.
- 전부 정상이면 한 줄로 끝낸다. 문제가 없는데 길게 쓰지 말 것.
