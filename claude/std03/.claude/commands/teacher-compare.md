---
description: 참가자 간 비교와 문항별 정답률·변별도 분석, 재검토 후보 문항 도출 (선생님 모드 4단계)
argument-hint: "[라벨 둘 — 1:1 비교] [--items 문항분석만] [--min-n N 최소 노출 수 (기본 12)]"
allowed-tools: Bash, Read, Grep, Write, Edit
---

실행 인수: **$ARGUMENTS**

> 이 하네스는 위치 인수(`$1`)를 신뢰할 수 없다 — 0-based로 밀려 들어온다.
> **`$ARGUMENTS` 한 덩어리만 쓰고 라벨·옵션은 그 문자열에서 골라낼 것.**

`/teacher-stats`가 **각자를 재는** 단계였다면 여기는 **서로 견주는** 단계다. 그리고 시선을 한 번 뒤집어 **사람이 아니라 문항을 채점한다.**

## 두 갈래

| 갈래 | 묻는 것 | 산출 |
|---|---|---|
| **사람 비교** | 누가 누구보다 어디가 나은가, 누가 튀는가 | `analysis.json`의 `comparison` |
| **문항 분석** | 어떤 문제가 사람들을 무너뜨렸나, 그 문제는 쓸 만한가 | `analysis.json`의 `items` + `flags` |

`analysis.json`에 **덧붙여 쓴다**(따로 파일을 만들지 않는다). 화면이 파일 하나만 읽게 하려는 것이다. 3단계가 만든 키(`overall`/`participants`/`categories`/`difficulties`)는 **건드리지 않는다.**

> **성장 추이는 내지 않는다.** 판 하나가 참가자 하나이므로(2단계 규칙) 같은 사람의 판을 이어 붙이는 개념이 이 모델에 없다. `김철민_1`과 `김철민_2`는 서로 다른 두 참가자로 비교될 뿐, 한 사람의 전후로 읽히지 않는다.

---

## 1. 사람 비교

### (A) 분야별 강약 프로파일

참가자 × 분야 4칸 표를 만들되 **정답률 원값이 아니라 전체 평균 대비 편차**를 담는다. 원값은 "과학 55%"가 잘한 건지 못한 건지 알려 주지 않는다.

```
라벨        한국사    과학    세계지리   예술
김철민_1    +0.18   -0.12    +0.05   -0.09
```

### (B) 이상치

- **성적 이상치** — Q1−1.5×IQR 아래 / Q3+1.5×IQR 위. `|z| ≥ 2`도 함께 표시
- **속도 이상치** — 평균 문항 소요시간이 3초 미만. **찍었을 가능성**이라 성적 해석 전에 봐야 한다
- **불일치** — 정답률은 높은데 점수가 낮은 참가자. 쉬운 문제만 맞히고 어려운 문제를 놓친 것이다(배점이 최상 50 ~ 최하 10이라 갈린다). **이 조합이야말로 총점만 봐서는 안 보이는 것**이라 반드시 짚는다

> 이상치는 **알리기만 하고 판정하지 않는다.** 3초 미만이 찍기일 수도, 아는 문제만 나온 것일 수도 있다. 스크립트가 "이 사람은 찍었다"고 단정하면 안 된다.

### (C) 1:1 비교

인수에 라벨 둘을 주면 그 둘만 나란히 낸다. 점수·정답률·분야 4개·난이도 5개·소요시간을 양쪽으로 세우고 **차이가 큰 순으로 정렬**한다. 어디서 갈렸는지가 바로 보여야 한다.

**둘이 푼 문제가 다르다는 사실을 표 아래 반드시 적는다.** 한 판은 410문항에서 40개를 뽑은 것이라 두 사람의 시험지가 같지 않다. 이 한 줄이 없으면 표가 실제보다 훨씬 정밀해 보인다.

---

## 2. 문항 분석 — 사람 대신 문항을 채점한다

**v2 기록(`responses`)이 있어야 한다.** 없으면 이 절을 통째로 건너뛰고 그 사실을 보고한다.

| 지표 | 정의 | 읽는 법 |
|---|---|---|
| 노출 `n` | 이 문항이 출제된 판의 수 | 작으면 나머지 수를 믿을 수 없다 |
| 정답률 `p` | 맞힌 비율 | 높을수록 쉬움 (고전검사이론의 난이도 지수) |
| 변별도 `D` | 상위 27% 정답률 − 하위 27% 정답률 | 잘 본 판일수록 더 맞히는가 |
| 오답 분포 | 선택지 4개가 각각 몇 번 선택됐나 | 특정 오답이 정답을 이기면 문제가 있다 |

### 변별도는 근사다 — 반드시 밝힌다

교과서의 변별도는 **같은 시험지**를 친 집단을 총점으로 줄 세워 상하위 27%를 가른다. 이 게임은 **판마다 문제가 다르다** — 410문항에서 40개를 뽑으므로 두 판의 시험지가 같지 않다.

그래서 이렇게 한다: **그 문항을 본 응답만 모아, 그 응답이 나온 판의 점수로 줄 세워 상하위 27%를 가른다.**

- 판 하나가 참가자 하나이므로 "그 사람의 대표 성적"을 고를 필요가 없다. **그 판의 점수를 그대로 쓴다** — 교과서 정의에 한 걸음 더 가깝다
- 그래도 **시험지가 서로 달라 점수가 엄밀히 같은 잣대가 아니다.** `analysis.json`과 보고에 `"method": "play-score-27pct"`로 근사임을 명시할 것. 근사를 정식 변별도로 내놓으면 멀쩡한 문항을 버리게 된다

### 표본이 모자라면 계산하지 않는다

한 판이 410문항 중 40개다. **문항 하나의 노출은 아주 적다.** 참가 30건이면 문항당 평균 노출은 `30×40÷410 ≈ 2.9`회다. 3회로 정답률을 말하는 것은 무의미하다.

| 지표 | 최소 | 못 미치면 |
|---|---|---|
| `p` | `n ≥ --min-n`(기본 12) | `null`, 판정 보류 |
| `D` | `n ≥ 30`이고 상·하위 군이 각 8건 이상 | `null` |
| 오답 분포 | `n ≥ 12` | 횟수만 내고 판정하지 않음 |
| `dead-distractor` | **오답이 15건 이상** | 판정하지 않음 |

> **`dead-distractor`만 전체 노출이 아니라 오답 수를 본다.** 92%가 맞히는 최하
> 문항은 오답 자체가 3건뿐이라 어떤 보기가 0인 게 당연하다. 전체 노출로 걸었더니
> 46건이 떴고 **전부 최하 난이도였다.**

**보고 첫 줄에 "판정 가능한 문항 수 / 출제된 문항 수"를 적는다.** 참가 30건으로는 대부분 `null`이 정상이고, 그게 결함이 아니라 **데이터가 아직 모자란 것**임을 알려야 한다.

### 자동 플래그 — 문턱이 아니라 검정으로 판정한다

**여기가 이 명령에서 가장 틀리기 쉬운 자리다.** 처음에는 "오답이 정답보다 많으면
고발"처럼 문턱만 두었는데, 결함을 하나도 심지 않은 데이터에서 🔴가 쏟아졌다.
아래 셋이 원인이었고 셋 다 실제로 겪은 뒤에 고쳤다.

**(1) 표본 크기를 무시했다.** 정답률 40%짜리 문항을 15명이 풀면 정답 6, 오답 3개가
각 3씩인데 흔들리면 그중 하나가 쉽게 6을 넘는다. `5 vs 4`, `8 vs 7`이 🔴로 찍혔다.
→ 정답과 최다 오답만 놓고 **부호검정**을 한다. 둘이 똑같이 매력적이었다면 이만큼
갈릴 확률을 본다.

| 관측 | p | 판정 |
|---|---|---|
| 5 vs 4 | 0.500 | 통과 |
| 8 vs 7 | 0.500 | 통과 |
| 9 vs 3 | 0.073 | 통과 |
| 14 vs 3 | 0.006 | 후보 |
| 20 vs 4 | 0.0008 | 후보 |

**(2) 다중검정을 무시했다.** 문항 340개에 α=0.05 검정을 돌리면 **아무 문제가 없어도
17건이 우연히 걸린다.** 실제로 보정 없이 돌렸을 때 뜬 🔴 3건은 전부 위양성이었다.
→ **벤저미니-호크버그(BH)**로 위양성 비율을 통제한다. 본페로니보다 덜 보수적이라
진짜를 덜 놓친다. `answer-suspect`와 `negative-D`가 각각 하나의 검정군이다.

**(3) 변별도의 흔들림을 무시했다.** `D`는 각 `k`건짜리 두 군의 정답률 차라서
`k=8`이면 표준오차가 0.24쯤이다 — **`D=-0.2`도 우연의 범위 안이다.** 그런데
`D < -0.1`이면 🔴를 붙이고 있었고, 결함 없는 데이터에서 5~13건이 떴다.
→ **두 비율 차의 단측 검정**을 하고 그 p값들도 BH로 보정한다.

| 플래그 | 판정 방법 | 뜻 | 이어지는 곳 |
|---|---|---|---|
| 🔴 `answer-suspect` | 부호검정 + BH | **정답이 둘일 수 있다** | 「교차 검증 가이드라인」 1번 — `SC-012`·`KH-009`가 이 모양이었다 |
| 🔴 `negative-D` | 두 비율 차 단측검정 + BH | 잘 본 판일수록 틀린다 | `/quiz-validate` |
| ⚠️ `low-D` | `D + 1.96·SE < 0.20` | 변별을 못 한다 | 문구 재검토 |
| ⚠️ `too-hard` | 윌슨 95% **상한** < 0.30, `n ≥ 30` | 찍기(25%) 수준 | `/quiz-validate` |
| ⚠️ `too-easy` | 윌슨 95% **하한** > 0.95, `n ≥ 30` | 아무도 안 틀린다 | 난이도 라벨 재배치 |
| ⚠️ `dead-distractor` | 오답 15건 이상인데 0인 보기 | 실질 3지선다 | `KH-093`이 이 모양이었다 |

**점추정만 보고 판정하는 플래그는 하나도 없다.** ⚠️조차 신뢰구간 전체가 문턱 밖에
있을 때만 붙인다. 표본이 작으면 구간이 넓어져 저절로 판정이 보류된다.

> **플래그는 고발이 아니라 후보다.** 자동으로 문항을 고치지 말 것. 이 명령이 하는
> 일은 **`/quiz-validate`에 넘길 목록을 뽑는 것**까지다. 내용의 옳고 그름은 사람이
> 출처 2개를 확인해 판정한다(CLAUDE.md 「퀴즈 문제 교차 검증 가이드라인」 4번).

### 그물이 실제로 작동하는지 확인하는 법

`/quiz-stats`가 χ² 그물을 합성 데이터로 시험한 것과 같은 방식이다. 브라우저가 없어
실제 응답을 만들 수 없으므로 **`tools/make_test_results.py`**로 가짜 결과를 만든다.

```bash
# 결함을 하나도 심지 않은 데이터 → 🔴가 0건이어야 한다 (위양성 확인)
python3 tools/make_test_results.py --bulk 400 --seed 777

# 한 문항을 '정답이 둘'인 것처럼 망가뜨린다 → 그 문항만 잡혀야 한다 (위음성 확인)
python3 tools/make_test_results.py --bulk 400 --seed 777 --trap SC-045
```

**양쪽을 다 봐야 한다.** 아무것도 안 잡는 그물과 다 잡는 그물은 둘 다 쓸모없다.
문항당 노출이 12회를 넘어야 판정이 시작되므로 `--bulk`는 300 이상이어야 한다.

---

## 3. `tools/results_compare.py`를 만든다(없으면)

```
python3 tools/results_compare.py [라벨1 라벨2] [--items] [--min-n N] [--anon]
```

- 입력은 `data/results/results.json` + `data/results/analysis.json`, 출력은 `analysis.json`에 **덮어쓰기가 아닌 키 추가**
- 표준 라이브러리만. 표 정렬은 `/quiz-stats`의 `east_asian_width` 방식
- 문항 텍스트는 `data/questions/*.js`에서 **정규식으로 `id`와 `question`만 긁어 온다** — 이 리포지토리에 JS 런타임이 없어 `import`할 수 없다. `validate_bank.py`가 이미 같은 일을 하니 그 파싱 방식을 따를 것

추가되는 키:

```json
{
  "comparison": {
    "profiles": [{ "id": "p001", "vsOverall": { "science": -0.12 } }],
    "outliers": {
      "high": ["p004"], "low": ["p019"],
      "fast": [{ "id": "p011", "meanElapsedMs": 2400 }],
      "mismatch": [{ "id": "p007", "accuracyRank": 4, "scoreRank": 14 }]
    },
    "head2head": null
  },
  "items": [
    { "id": "SC-012", "question": "…", "category": "science", "difficulty": "mid",
      "n": 23, "p": 0.30, "d": -0.12, "method": "play-score-27pct",
      "choiceCounts": [3, 14, 5, 1], "answerIndex": 0,
      "flags": ["answer-suspect", "negative-D"] }
  ],
  "itemCoverage": { "seen": 380, "judged": 41, "minN": 12 },
  "itemTests": { "answerSuspectTested": 29, "dTested": 12,
                 "method": "benjamini-hochberg", "alpha": 0.05 },
  "flags": [{ "level": "red", "code": "answer-suspect", "id": "SC-012",
              "note": "보기2가 정답보다 많이 선택됨 (14 vs 3, 부호검정 p=0.00640, BH 통과)" }]
}
```

## 4. 돌린다

```bash
cd quiz-game && python3 tools/results_compare.py $ARGUMENTS
```

## 5. 검증

```bash
cd quiz-game && python3 - <<'PY'
import json, sys
from collections import Counter
a = json.load(open('data/results/analysis.json', encoding='utf-8'))
r = json.load(open('data/results/results.json', encoding='utf-8'))
ok = True
def chk(l, c, h=''):
    global ok; print(('  ✅ ' if c else '  ❌ ') + l + ('' if c else '  ← ' + h)); ok = ok and c

chk('3단계 결과가 살아 있다',
    all(k in a for k in ('overall', 'participants', 'categories', 'difficulties')),
    'compare가 stats 결과를 덮어썼다')
chk('comparison 추가됨', 'comparison' in a)
chk('성장 추이 없음', 'growth' not in a.get('comparison', {}),
    '판 하나가 참가자 하나라 성장은 이 모델에 없다')

items = a.get('items', [])
v2 = [x for x in r['records'] if isinstance(x.get('responses'), list)]
if not v2:
    print('  ℹ️  v2 기록이 없어 문항 분석을 건너뛴 것이 정상이다')
    chk('건너뛴 사실이 기록됨', a.get('itemCoverage', {}).get('judged', 0) == 0)
else:
    minn = a.get('itemCoverage', {}).get('minN', 12)
    chk('노출 부족 문항은 p가 null',
        all(i['p'] is None for i in items if i['n'] < minn),
        '표본이 모자란데 정답률을 냈다')
    chk('p 범위 0..1', all(0 <= i['p'] <= 1 for i in items if i['p'] is not None))
    chk('D 범위 -1..1', all(-1 <= i['d'] <= 1 for i in items if i['d'] is not None))
    chk('근사 방법 명시',
        all(i.get('method') == 'play-score-27pct' for i in items if i.get('d') is not None),
        '변별도가 근사임을 밝혀야 한다')
    seen = Counter(x['id'] for rec in v2 for x in rec['responses'])
    bad = [i['id'] for i in items if seen.get(i['id'], 0) != i['n']]
    chk('노출 수 재계산 일치', not bad, f'{bad[:3]}')
    bad2 = [i['id'] for i in items if i.get('choiceCounts') and sum(i['choiceCounts']) != i['n']]
    chk('선택지 합 = 노출 수', not bad2, f'{bad2[:3]}')
    chk('다중검정 보정을 걸었다',
        a.get('itemTests', {}).get('method') == 'benjamini-hochberg',
        '보정 없이 340번 검정하면 우연히 17건이 걸린다')
    chk('v1 기록은 문항 분석에서 빠졌다',
        sum(i['n'] for i in items) == sum(len(x['responses']) for x in v2))

cov = a.get('itemCoverage', {})
print(f"\n출제된 문항 {cov.get('seen', 0)}개 중 판정 가능 {cov.get('judged', 0)}개 (최소 노출 {cov.get('minN')}회)")
red = [f for f in a.get('flags', []) if f['level'] == 'red']
print(f"🔴 {len(red)}건 / ⚠️ {len(a.get('flags', [])) - len(red)}건")
sys.exit(0 if ok else 1)
PY
```

## 6. 보고

1. **판정 가능 범위를 먼저** — "출제 380문항 중 41개만 판정 가능(노출 12회 이상)". 이걸 앞에 두지 않으면 뒤 숫자가 과대평가된다
2. 강약 프로파일 표 (전체 평균 대비 편차)
3. 이상치 — 성적 / 속도 / **정답률-점수 불일치**
4. 1:1 비교 (라벨 둘을 준 경우)
5. **재검토 후보 문항** — 🔴 먼저, id·문항 앞머리·플래그·근거 수치와 함께
6. 후보가 있으면 다음 줄을 그대로 안내한다:
   ```
   /quiz-validate 과학        ← 후보가 몰린 분야를 내용 검증
   /quiz-range 과학 10 30     ← 그 구간의 분포 확인
   ```

> **선생님 모드가 문제 은행 품질로 되먹임된다.** 실제 응답만이 알려 줄 수 있는 것이 있다 — 「교차 검증 가이드라인」을 아무리 꼼꼼히 봐도 **아무도 못 맞히는 문항**과 **정답이 둘인 문항**은 사람 눈으로 다 걸러지지 않는다. `SC-012`와 `KH-009`가 그랬다.

---

## 다음

**`/teacher-dashboard`**가 이 `analysis.json`을 화면으로 그린다.
