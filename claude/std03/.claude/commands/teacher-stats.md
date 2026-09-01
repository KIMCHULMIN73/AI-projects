---
description: 결과 묶음에서 전체·참가자별·분야별·난이도별 통계를 계산해 analysis.json으로 굳힌다 (선생님 모드 3단계)
argument-hint: "[--top N 표에 몇 명까지 | --anon 이름 가림]"
allowed-tools: Bash, Read, Grep, Write, Edit
---

실행 인수: **$ARGUMENTS**

> 이 하네스는 위치 인수(`$1`)를 신뢰할 수 없다 — 0-based로 밀려 들어온다.
> **`$ARGUMENTS` 한 덩어리만 쓰고 옵션은 그 문자열에서 골라낼 것.**

`data/results/results.json`을 읽어 **모든 통계를 여기서 한 번에 계산하고** `data/results/analysis.json`으로 굳힌다.

## 무엇을 알려 주는 통계인가

**"전체 안에서 내가 어디쯤인가."** 그 이상을 하지 않는다.

전체 인원과 평균·표준편차가 있으면 각자의 위치가 나온다 — z-점수로 평균에서 몇 표준편차 떨어져 있는지, 백분위로 몇 %가 나보다 아래인지, 석차로 몇 번째인지. **등급 같은 제도적 칸 나누기는 두지 않는다.** 이 게임에 그럴 근거가 없고, z·백분위·석차만으로 위치는 충분히 드러난다.

**판 하나가 참가자 하나다.** 같은 이름이 여러 번 쳤으면 `김철민_1`·`김철민_2`가 각각 한 명으로 센다(2단계에서 정해진다). 그래서 이 단계에는 "여러 판 중 무엇을 그 사람 성적으로 볼 것인가" 같은 문제가 아예 없다. **점수는 그냥 그 판의 점수다.**

## 이 프로젝트에서 가장 중요한 설계 규칙 — 계산은 여기 한 곳에만 있다

대시보드(`/teacher-dashboard`)는 **계산하지 않는다.** 이 파일이 만든 `analysis.json`을 **그리기만** 한다.

> **왜 JS로도 짜지 않는가.** 이 리포지토리에는 이미 같은 로직이 두 언어에 사는 자리가 있다 — `validate_bank.py`가 `storage.js`의 샘플링을 파이썬으로 이식해 뒀고, CLAUDE.md는 **"자동으로 동기화되지 않는다"**고 경고한다. 통계는 훨씬 넓어서 두 벌로 두면 반드시 갈라지고, **갈라진 순간 화면과 터미널이 서로 다른 1등을 말한다.** 그래서 계산은 파이썬 한 곳, 화면은 렌더 전용이다. 기존 모듈2의 "뷰는 그리기만 한다" 규율과 같은 모양이다.
>
> 대가는 있다. **새 데이터가 들어오면 이 명령을 다시 돌려야 화면이 갱신된다.** 그 불편을 없애려고 `/teacher-mode`가 전 단계를 한 줄로 묶는다.

## `/quiz-stats`와 헷갈리지 말 것

| 명령 | 보는 대상 | 묻는 것 |
|---|---|---|
| `/quiz-stats` | **문제 은행** | 문항이 난이도·정답 위치에 고르게 퍼졌나 |
| `/teacher-stats` | **참가 성적** | 누가 잘했고 어디가 약한가 |

---

## 1. 계산 목록

### (A) 전체

| 지표 | 비고 |
|---|---|
| 참가자 수 · 서로 다른 이름 수 | 이름 수는 참고용. 통계는 전부 참가자 단위 |
| 평균 · 중앙값 · 최빈 | |
| **표준편차 · 분산** | **모표준편차(ddof=0)** — 이 묶음이 곧 전부지 표본이 아니다 |
| 최고 · 최저 · 범위 | |
| 사분위수 Q1/Q2/Q3 · IQR | |
| 점수 히스토그램 | 구간은 데이터에서 정하지 말고 **고정 폭**으로 |
| 평균 정답률 · 평균 소요시간 | 소요시간은 v2 기록만 |

### (B) 참가자별

| 지표 | 비고 |
|---|---|
| 점수 · 정답 수 · 정답률 | |
| **z-점수** `(x-μ)/σ` | 평균에서 몇 표준편차 떨어져 있나. **σ=0이면 0으로 둔다** — 전원 동점이면 나눌 수 없다 |
| **백분위** | 정의를 하나로 못 박는다: **자기보다 낮은 사람 수 / (전체−1) × 100**. 1등 100, 꼴찌 0 |
| **석차** | **동점은 같은 등수, 다음은 건너뛴다**(1,1,3). 게임 랭킹은 동점을 최신순으로 갈랐지만 그건 표시용이고 석차는 갈라선 안 된다 |
| 분야별 정답률 4개 | |
| 난이도별 정답률 5개 | v2만 |
| 강점·약점 분야 | 전체 평균 대비 편차가 가장 큰 쪽/작은 쪽 |
| 평균 문항 소요시간 | v2만 |

### (C) 분야별 · 난이도별 (전체 집계)

- 분야 4개: 평균 정답률, 표준편차, 최고/최저 참가자, 전체 평균 대비 편차
- 난이도 5개: **실제 정답률**. 이게 설계 검증이다 — `bottom`(최하)이 `top`(최상)보다 정답률이 낮으면 **난이도 라벨이 뒤집힌 것**이다. CLAUDE.md 사후 수정 §6에서 실제로 겪은 일이라 다시 확인할 값어치가 있다.

### (D) 표본이 모자라면 판정하지 않는다

`/quiz-stats`가 "20문항 미만은 판정 보류"를 두는 것과 같은 이유다. 숫자는 표본 크기를 숨긴다.

| 지표 | 최소 조건 | 못 미치면 |
|---|---|---|
| z-점수 · 백분위 | 참가자 3명 | `null` |
| 난이도별 정답률 | 그 난이도 응답 20건 | `null` + 사유 |

`null`과 `0`은 전혀 다르다. **못 구한 것을 0으로 채우지 말 것.** 화면은 `null`을 `—`로 그린다.

---

## 2. `tools/results_stats.py`를 만든다(없으면)

```
python3 tools/results_stats.py [--top N] [--anon] [--out data/results/analysis.json]
```

- **표준 라이브러리만 쓴다.** numpy·pandas 없다(이 환경 제약). `statistics` 모듈과 손계산으로 충분하다.
- 터미널에 표를 내고 **동시에** `analysis.json`을 쓴다. 화면에 보이는 수와 파일에 든 수가 갈라지면 안 되므로 **한 번 계산해서 둘 다에 쓴다.**
- `--anon`이면 라벨을 `참가자A`… 로 바꿔 **파일에도 가명으로 쓴다.** 매핑은 남기지 않는다(남기면 가리는 의미가 없다).
- 한글 표 정렬은 `/quiz-stats`가 쓰는 `east_asian_width` 폭 계산을 그대로 가져다 쓴다.

### `analysis.json` — `quiz-analysis/v1`

```json
{
  "schema": "quiz-analysis/v1",
  "generatedAt": "2026-09-01T12:35:00.000Z",
  "coverage": { "participants": 24, "distinctNames": 19, "v2": 21, "v1": 3 },
  "overall": {
    "mean": 268.3, "median": 270, "mode": 240, "stdev": 61.2, "variance": 3745.4,
    "min": 120, "max": 410, "range": 290,
    "q1": 220, "q3": 310, "iqr": 90,
    "meanAccuracy": 0.62, "meanDurationMs": 498000,
    "histogram": [{ "from": 100, "to": 149, "count": 1 }]
  },
  "participants": [
    { "id": "p001", "label": "김철민_1", "score": 340,
      "correctCount": 28, "accuracy": 0.7,
      "z": 1.17, "percentile": 87.0, "rank": 3,
      "byCategory": { "korean_history": 0.8 },
      "byDifficulty": { "bottom": 1.0, "low": 0.85, "mid": 0.7, "high": 0.45, "top": 0.2 },
      "strongest": "korean_history", "weakest": "science",
      "meanElapsedMs": 8200 }
  ],
  "categories": [
    { "key": "science", "label": "과학 상식", "meanAccuracy": 0.55,
      "stdev": 0.18, "best": "p004", "worst": "p019", "vsOverall": -0.07 }
  ],
  "difficulties": [
    { "key": "bottom", "label": "최하", "actualAccuracy": 0.91, "n": 240,
      "inverted": false }
  ],
  "notes": []
}
```

문항별 분석(`items`)은 **여기서 하지 않는다** — `/teacher-compare`의 몫이다. 이 단계는 **사람에 대한 통계**, 다음 단계는 **비교와 문항에 대한 통계**로 갈라 둔다.

## 3. 돌린다

```bash
cd quiz-game && python3 tools/results_stats.py $ARGUMENTS
```

## 4. 검증

```bash
cd quiz-game && python3 - <<'PY'
import json, sys, os, statistics as st
p = 'data/results/analysis.json'
if not os.path.exists(p): print('❌ analysis.json이 없다'); sys.exit(1)
a = json.load(open(p, encoding='utf-8'))
r = json.load(open('data/results/results.json', encoding='utf-8'))
ok = True
def chk(l, c, h=''):
    global ok; print(('  ✅ ' if c else '  ❌ ') + l + ('' if c else '  ← ' + h)); ok = ok and c

chk('스키마 표기', a.get('schema') == 'quiz-analysis/v1')
P = a.get('participants', [])
chk('참가자 수 = 판 수', len(P) == len(r.get('records', [])),
    f"analysis {len(P)} vs results {len(r.get('records', []))} — 같은 이름을 합쳤을 수 있다")

# 통계를 검사 쪽에서 독립적으로 다시 구해 대조한다
vals = [x['score'] for x in P]
o = a['overall']
chk('평균 재계산 일치', abs(st.mean(vals) - o['mean']) < 0.01, f"{st.mean(vals):.2f} vs {o['mean']}")
chk('중앙값 재계산 일치', abs(st.median(vals) - o['median']) < 0.01)
if len(vals) > 1:
    chk('표준편차는 모표준편차(ddof=0)', abs(st.pstdev(vals) - o['stdev']) < 0.01,
        f"표본표준편차를 쓴 듯: pstdev {st.pstdev(vals):.2f} / stdev {st.stdev(vals):.2f}")
chk('최고/최저/범위 일치',
    o['min'] == min(vals) and o['max'] == max(vals) and o['range'] == max(vals) - min(vals))
chk('히스토그램 합 = 참가자 수', sum(b['count'] for b in o['histogram']) == len(P))

# 석차: 동점 같은 등수, 다음은 건너뛰기
srt = sorted(P, key=lambda x: -x['score'])
exp, prev, pr = {}, None, 0
for i, x in enumerate(srt, 1):
    if x['score'] != prev: pr, prev = i, x['score']
    exp[x['id']] = pr
chk('석차 규칙(동점 동순위·건너뛰기)', all(x['rank'] == exp[x['id']] for x in P))

# 백분위·z 정의
if len(P) >= 3:
    n, mu, sd = len(P), st.mean(vals), st.pstdev(vals)
    bad = [x['label'] for x in P if x.get('percentile') is not None
           and abs(x['percentile'] - sum(1 for y in P if y['score'] < x['score']) / (n - 1) * 100) > 0.6]
    chk('백분위 정의 일치', not bad, f'{bad[:3]}')
    chk('최고점 백분위 100', abs(max(x['percentile'] for x in P) - 100) < 0.6)
    chk('최저점 백분위 0', abs(min(x['percentile'] for x in P)) < 0.6)
    badz = [x['label'] for x in P if x.get('z') is not None and sd > 0
            and abs(x['z'] - (x['score'] - mu) / sd) > 0.01]
    chk('z-점수 정의 일치', not badz, f'{badz[:3]}')
else:
    chk('3명 미만이면 z·백분위는 null',
        all(x.get('z') is None and x.get('percentile') is None for x in P))

chk('정답률 0..1', all(0 <= x['accuracy'] <= 1 for x in P))
chk('등급 없음', not any('grade' in x for x in P), '제도적 등급은 두지 않는다')
# 구버전 기록에 없는 값을 0으로 채우면 그 판이 '전부 오답'으로 읽힌다.
chk('v1 기록은 byDifficulty가 null',
    all(x['byDifficulty'] is None for x in P
        if next(y for y in r['records'] if y['participantId'] == x['id'])['responses'] is None),
    '구버전 기록에 없는 값을 채워 넣었다')

inv = [d['key'] for d in a.get('difficulties', []) if d.get('inverted')]
if inv: print(f'\n⚠️  난이도 라벨 역전: {inv} — 실제 정답률이 라벨 순서와 어긋난다')
for n in a.get('notes', []): print('  ℹ️ ', n)
sys.exit(0 if ok else 1)
PY
```

**검증이 스스로 다시 계산해서 대조한다.** 스크립트가 낸 수를 그대로 믿고 형식만 보면 검증이 아니다.

## 5. 보고

- 한 줄 요약: 참가자 N명(서로 다른 이름 M개) · 평균 μ(σ) · 중앙값 · 범위
- 순위표 (기본 상위 10, `--top N`으로 조절): 석차 · 라벨 · 점수 · 정답률 · **z** · **백분위**
- 분야별 평균 정답률 4줄 — 가장 약한 분야를 짚는다
- 난이도별 실제 정답률 5줄 — **역전이 있으면 크게 알린다**
- `null`로 남긴 지표와 그 사유

---

## 다음

**`/teacher-compare`**가 참가자 간 비교와 문항별 분석을 얹는다.
