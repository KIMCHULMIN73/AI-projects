---
description: 지정한 분야의 문항 번호 범위에서 난이도 분포와 정답 위치 분포를 확인한다
argument-hint: "<분야> <시작번호> <끝번호>  또는  <KH-010> <KH-020>"
allowed-tools: Bash, Read, Grep
---

검사 범위: **$ARGUMENTS**

문항을 새로 쓰거나 한 구간을 손본 뒤 **그 구간만** 들여다보는 명령이다. `validate_bank.py`는 분야 100문항 전체의 합계만 보므로 "새로 추가한 KH-081~KH-100 스무 개가 한쪽으로 쏠렸는지"는 잡아내지 못한다. 이 명령이 그 자리를 메운다.

보는 것은 둘이다.

1. **난이도 분포** — 최상/상/중/하/최하가 은행 기준 비율(10:20:40:20:10)에서 얼마나 벗어났는지
2. **정답 위치(`answerIndex`) 분포** — 0~3번에 고르게 퍼져 있는지

> 정답 위치 쏠림은 이 프로젝트가 실제로 크게 데인 적이 있다. 400문항으로 확장했을 때 **317개의 정답이 0번**이라 첫 보기만 찍어도 79점이 나왔다(CLAUDE.md 사후 수정 이력 §7). `validate_bank.py`는 이 검사를 하지 않는다.

## 인수

**분야는 반드시 있어야 한다.** 문항 id가 분야별 번호(`KH-001`~`KH-100`)라 숫자만으로는 어느 분야인지 정해지지 않기 때문이다.

| 형태 | 예 | 뜻 |
|---|---|---|
| 분야 + 번호 2개 | `/quiz-range 한국사 10 20` | KH-010 ~ KH-020 |
| id 2개 | `/quiz-range KH-010 KH-020` | 같음 (분야가 id에 들어 있다) |
| 분야만 | `/quiz-range 과학` | SC-001 ~ SC-100 (분야 전체) |
| 숫자만 | `/quiz-range 10 20` | **거부** — 어느 분야인지 되묻고 멈춘다 |

분야 표기는 `/quiz-validate`와 같다: `한국사`/`korean-history`/`KH`, `과학`/`science`/`SC`, `세계지리`/`지리`/`world-geography`/`WG`, `예술`/`문화`/`arts-culture`/`AC`.

시작·끝 번호는 순서가 뒤집혀 있어도(`20 10`) 알아서 바로잡는다.

## 1. 세기

```bash
cd quiz-game && python3 - "$ARGUMENTS" <<'PY'
import re, sys, unicodedata
from collections import Counter

def w(t):   # 한글·한자는 터미널에서 2칸을 먹는다
    return sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in str(t))
def pad(t, n, right=False):
    sp = ' ' * max(0, n - w(t))
    return sp + str(t) if right else str(t) + sp

OBJ = re.compile(r"\{\s*\n\s*id:\s*'([^']+)',(.*?)\n  \},", re.S)
DIF = re.compile(r"difficulty:\s*'(\w+)'")
ANS = re.compile(r"answerIndex:\s*(\d)")
QUE = re.compile(r"\n\s*question:\s*(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')")

ALIAS = {
    'korean-history':'korean-history','korean_history':'korean-history','한국사':'korean-history','kh':'korean-history',
    'science':'science','과학':'science','sc':'science',
    'world-geography':'world-geography','world_geography':'world-geography',
    '세계지리':'world-geography','지리':'world-geography','wg':'world-geography',
    'arts-culture':'arts-culture','arts_culture':'arts-culture',
    '예술':'arts-culture','문화':'arts-culture','시사':'arts-culture','ac':'arts-culture',
}
CATNAME = {'korean-history':'한국사','science':'과학',
           'world-geography':'세계지리','arts-culture':'예술·문화·시사'}
PREFIX  = {'KH':'korean-history','SC':'science','WG':'world-geography','AC':'arts-culture'}

# 난이도: 코드 → (표기, 은행 기준 비율 %, 정답 배점)
DIFS = [('top','최상',10,50), ('high','상',20,40), ('mid','중',40,30),
        ('low','하',20,20), ('bottom','최하',10,10)]

args = sys.argv[1].split() if len(sys.argv) > 1 else []
if not args:
    print("인수가 없습니다.  예: /quiz-range 한국사 10 20")
    sys.exit(1)

stem, lo, hi = None, None, None
ids = [a.upper() for a in args if re.fullmatch(r'(?i)(KH|SC|WG|AC)-\d{1,3}', a)]
if len(ids) >= 2:                                    # KH-010 KH-020 형태
    p = {i.split('-')[0] for i in ids[:2]}
    if len(p) > 1:
        print(f"두 id의 분야가 다릅니다: {ids[0]}, {ids[1]}")
        print("한 분야 안의 범위만 검사합니다.")
        sys.exit(1)
    stem = PREFIX[ids[0].split('-')[0]]
    lo, hi = sorted(int(i.split('-')[1]) for i in ids[:2])
else:                                                # 분야 + 번호 형태
    unknown = []
    for a in args:
        if stem is None and ALIAS.get(a.lower()):
            stem = ALIAS[a.lower()]
        elif re.fullmatch(r'\d{1,3}', a):
            (lo, hi) = (int(a), hi) if lo is None else (lo, int(a))
        else:
            unknown.append(a)
    if stem is None:
        nums = [a for a in args if re.fullmatch(r'\d{1,3}', a)]
        if unknown:
            print(f"분야를 알 수 없습니다: {', '.join(repr(u) for u in unknown)}")
        elif nums:
            print(f"분야가 빠졌습니다. 번호({', '.join(nums)})만으로는 어느 분야인지 정해지지 않습니다.")
            print("  문항 id가 분야별 번호(KH-001~KH-100)이기 때문입니다.")
            print(f"  예: /quiz-range 한국사 {nums[0]} {nums[-1] if len(nums)>1 else nums[0]}")
            print(f"      /quiz-range KH-{int(nums[0]):03d} KH-{int(nums[-1] if len(nums)>1 else nums[0]):03d}")
        else:
            print("분야가 빠졌습니다.")
        print("\n받는 값: 한국사 | 과학 | 세계지리 | 예술")
        sys.exit(2)
    if lo is None:
        lo, hi = 1, 100
    elif hi is None:
        hi = lo
    lo, hi = sorted((lo, hi))

src = open(f'data/questions/{stem}.js', encoding='utf-8').read()
rows = []
for m in OBJ.finditer(src):
    num = int(m.group(1).split('-')[1])
    if lo <= num <= hi:
        rows.append((m.group(1), DIF.search(m.group(2)).group(1),
                     int(ANS.search(m.group(2)).group(1)),
                     QUE.search(m.group(2)).group(1)[1:-1]))

pre = [k for k, v in PREFIX.items() if v == stem][0]
print(f"검사 범위: {CATNAME[stem]}  {pre}-{lo:03d} ~ {pre}-{hi:03d}")
if not rows:
    print("\n이 범위에 해당하는 문항이 없습니다.")
    sys.exit(0)
n = len(rows)
print(f"문항 수  : {n}개\n")

# ── 난이도 분포 ──────────────────────────────────────────────
dc = Counter(d for _, d, _, _ in rows)
def head(first):
    return (pad(first, 8) + pad('개수', 6, 1) + pad('비율', 9, 1)
            + pad('기준', 8, 1) + pad('차이', 9, 1) + '    분포')
print("── 난이도 분포 " + "─"*46)
print(head('난이도'))
pts = 0
for code, label, pct, point in DIFS:
    c = dc.get(code, 0)
    got, exp = c / n * 100, pct
    pts += c * point
    d = got - exp
    mark = '  ' if abs(d) <= 8 else ('!!' if abs(d) > 20 else ' !')
    print(pad(label, 8) + f"{c:>6}{got:>8.1f}%{exp:>7}%{d:>+8.1f}%{mark} {'█'*round(got/4)}")
extra = set(dc) - {c for c, _, _, _ in DIFS}
if extra:
    print(f"  ⚠ 알 수 없는 난이도 값: {', '.join(sorted(extra))}")
print(f"\n평균 배점: {pts/n:.1f}점  (이 범위만 나온다면 정답 1문항당 평균)")

# ── 정답 위치 분포 ───────────────────────────────────────────
ac = Counter(a for _, _, a, _ in rows)
print("\n── 정답 위치(answerIndex) 분포 " + "─"*31)
print(head('위치'))
worst = 0
for i in range(4):
    c = ac.get(i, 0)
    got = c / n * 100
    worst = max(worst, got)
    d = got - 25
    mark = '  ' if abs(d) <= 10 else ('!!' if abs(d) > 20 else ' !')
    print(pad(f'{i}번', 8) + f"{c:>6}{got:>8.1f}%{25:>7}%{d:>+8.1f}%{mark} {'█'*round(got/4)}")

print()
if n < 8:
    print(f"※ {n}문항은 분포를 논하기에 적다. 참고만 할 것.")
elif worst > 50:
    top = max(ac, key=lambda k: ac[k])
    print(f"🔴 정답이 {top}번에 {worst:.0f}% 쏠렸다. {top}번만 찍어도 {worst:.0f}점이 나온다 — 반드시 섞을 것.")
elif worst > 40:
    top = max(ac, key=lambda k: ac[k])
    print(f"⚠️ 정답이 {top}번에 {worst:.0f}% 몰려 있다. 문항을 더 추가하기 전에 섞어 두는 편이 좋다.")
else:
    print("✅ 정답 위치는 고르게 퍼져 있다.")

# ── 문항 목록 ────────────────────────────────────────────────
LBL = {c: l for c, l, _, _ in DIFS}
print("\n── 문항 목록 " + "─"*48)
for qid, d, a, q in rows:
    print(f"{qid}  " + pad(LBL.get(d, d), 5) + f"정답{a}번  {q[:44]}{'…' if len(q) > 44 else ''}")
PY
```

`기준` 칸은 은행 전체의 목표 비율이다. **한 구간이 기준과 다른 것 자체는 결함이 아니다** — 파일 안에서 난이도를 쉬운 것부터 어려운 순(bottom → top)으로 모아 두었기 때문에, 앞번호 구간은 최하·하가 많고 뒷번호 구간은 상·최상이 많은 것이 정상이다.

## 2. 읽는 법

| 무엇 | 언제 문제인가 |
|---|---|
| **난이도 분포** | 한 분야 **100문항 전체**를 볼 때만 기준과 맞아야 한다(`top:10 high:20 mid:40 low:20 bottom:10`). 어긋나면 `getQuizSet()`의 할당량을 채우지 못해 인접 난이도 보충 경로를 타게 된다 |
| **정답 위치 분포** | **구간 크기와 무관하게** 고르게 퍼져 있어야 한다. 한 번호에 40%를 넘으면 찍기가 통한다 |

난이도는 구간별로 치우쳐도 되지만 **정답 위치는 어느 구간에서도 치우치면 안 된다** — 이 둘의 차이가 이 명령을 읽는 핵심이다.

문항 목록에서 `정답0번`이 연달아 나오는 구간이 보이면 그것도 쏠림이다. 비율이 정상이어도 **연속으로 몰려 있으면** 플레이 중에 눈치채기 쉽다.

## 3. 고칠 때

정답 위치를 섞을 때는 **보기 순서를 바꾸고 `answerIndex`를 다시 계산한다.** 주의할 것이 둘 있다.

- **네 보기가 모두 숫자면(연도·개수 등) 섞지 말고 오름차순을 유지할 것.** §7에서 한 번 겪었다 — 기계적으로 섞었더니 `1945년, 1910년, 1929년, 1919년`처럼 되어 눈에 거슬렸다. 29문항을 되돌려야 했다.
- 보기를 건드렸으면 **같은 것을 가리키는 보기가 생기지 않았는지** 확인할 것. `이벌찬`과 `각간`처럼 이름만 다른 동일물은 `validate_bank.py`의 중복 검사(문자열 비교)를 통과해 버린다(§10).

고친 뒤에는 이 명령을 다시 돌리고, 은행 전체도 함께 확인한다.

```bash
cd quiz-game && python3 tools/validate_bank.py
```
