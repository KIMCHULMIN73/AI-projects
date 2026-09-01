---
description: 흩어진 결과 파일을 묶음 하나로 모으고, 판마다 참가자 라벨을 붙인다 (선생님 모드 2단계)
argument-hint: "[--dry-run 반영 없이 결과만] [--strict 경고도 실패로]"
allowed-tools: Bash, Read, Grep, Write, Edit
---

실행 인수: **$ARGUMENTS**

> 이 하네스는 위치 인수(`$1`)를 신뢰할 수 없다 — 0-based로 밀려 들어온다.
> **`$ARGUMENTS` 한 덩어리만 쓰고 옵션은 그 문자열에서 골라낼 것.**

`inbox/`에 쌓인 결과 파일 여러 개를 **`data/results/results.json` 하나로** 합친다. 뒤 세 단계는 이 파일 하나만 본다.

## 세는 단위 — 판 하나가 참가자 하나다

**같은 이름으로 여러 번 쳐도 각각 다른 사람으로 센다.** 같은 사람의 여러 판을 하나로 묶지 않는다.

```
김철민이 3판을 쳤다  →  김철민_1, 김철민_2, 김철민_3   (참가자 3명)
이수현이 1판을 쳤다  →  이수현                        (참가자 1명)
```

- **1회뿐이면 접미사를 붙이지 않는다.** 2회 이상일 때만 `_1`부터 붙인다.
- 번호는 **`playedAt` 오름차순** — 먼저 친 판이 `_1`이다.
- 만들어낸 라벨이 다른 원본 이름과 겹치면(누군가 실제로 `김철민_1`을 입력한 경우) 겹치지 않을 때까지 `_`를 덧붙인다.

**이 규칙에서 따라 나오는 것들.**

- **이름을 뭉치지 않는다.** `철민`과 `김철민`이 같은 사람인지 판정할 이유가 없어졌다 — 어차피 판마다 따로 세니까. 자동 병합도, 승인 절차도, `aliases.json`도 두지 않는다.
- **성장 추이를 내지 않는다.** 같은 사람의 판을 이어 붙이는 개념이 없으므로 "첫 판 대비 얼마나 늘었나"는 이 모델에서 성립하지 않는다.
- **라벨은 영속 식별자가 아니다.** 김철민이 한 판만 쳤을 때는 `김철민`이지만, 나중에 한 판을 더 내면 `김철민_1`·`김철민_2`로 바뀐다. **기록의 신원은 여전히 `name + playedAt`이고**, 라벨은 표시용이다. 문서나 다른 파일이 라벨로 참가자를 지목하지 말 것.

## 합치는 건 쉽고, 안 틀리게 합치는 게 어렵다

파일을 이어 붙이는 것 자체는 열 줄이면 된다. 실제로 사람을 잡는 건 둘이다.

**(1) 같은 판이 두 번 들어온다.** 내보내기를 두 번 누르면 두 파일에 **같은 판이 모두** 들어 있다(반출은 그 시점까지의 전체 기록을 담으므로). 그대로 합치면 참가자 수가 부풀고 평균이 그쪽으로 끌려간다. → **`name + playedAt`이 같으면 같은 판이다.** `playedAt`은 `finish()`가 딱 한 번 찍는 ISO 시각이라 신원 노릇을 할 수 있다.

> 같은 이름을 여러 사람으로 세는 것과 **모순되지 않는다.** 다른 `playedAt` = 다른 판 = 다른 참가자, 같은 `playedAt` = 같은 판이 두 번 들어온 것 = 중복.

**(2) 깨진 파일이 조용히 섞인다.** 손으로 편집하다 만 JSON, 다른 게임의 파일, `accuracy`가 백분율(70)로 든 파일. → **레코드 단위로 검사하고, 버린 것은 버렸다고 말한다.** 조용히 넘기면 없어진 기록을 아무도 모른다.

## 묶음 스키마 — `quiz-results/v1`

```json
{
  "schema": "quiz-results/v1",
  "mergedAt": "2026-09-01T12:30:00.000Z",
  "participants": [
    { "id": "p001", "label": "김철민_1", "name": "김철민",
      "playedAt": "2026-09-01T11:50:00.000Z" }
  ],
  "records": [
    { "participantId": "p001", "label": "김철민_1", "name": "김철민",
      "score": 340, "correctCount": 28, "total": 40, "accuracy": 0.7,
      "byCategory": {}, "playedAt": "...", "durationMs": 512340,
      "responses": [ ... ] }
  ],
  "sources": [
    { "file": "quiz-result-김철민-20260901-1150.json", "records": 3,
      "added": 3, "duplicate": 0, "invalid": 0 }
  ],
  "warnings": []
}
```

- `participants`와 `records`는 **1:1이다.** 판 하나에 참가자 하나이므로 사실상 같은 목록의 두 얼굴이고, `participants`는 라벨을 찾기 쉬우라고 둔 색인이다.
- `responses`는 **v1 기록이면 `null`이다.** `[]`로 바꾸지 말 것 — `/teacher-export`에 적은 이유 그대로, 빈 배열은 "전부 오답"으로 읽혀 평균을 망친다.
- `id`는 `label` 가나다순으로 `p001`부터. **라벨이 바뀌면 id도 바뀐다** — 파일 안에서만 쓰이고 아무 문서도 id로 참가자를 지목하지 않으므로 비용이 없다(문항 id 재부여와 다른 점이다).

---

## 1. 받는 곳을 만든다

```bash
cd quiz-game && mkdir -p data/results/inbox data/results/archive && \
printf '*\n' > data/results/.gitignore && \
ls -la data/results/ && echo "── inbox ──" && ls -1 data/results/inbox/ 2>/dev/null | head -50 && \
echo "inbox 파일 수: $(ls -1 data/results/inbox/*.json 2>/dev/null | wc -l)"
```

> **성적은 git에 올리지 않는다.** 이름과 점수가 리포지토리에 들어가면 되돌리기 어렵다. `data/results/.gitignore`는 **`*` 한 줄뿐이고 자기 자신까지 무시한다** — `tools/.backups/`와 같은 방식이다. **`!.gitignore`로 예외를 두면 안 된다**: 그러면 그 파일만 추적되지 않은 채 남아 폴더가 통째로 `git status`에 노출된다.
>
> 이 폴더는 `results_collect.py`가 처음 돌 때 스스로 만든다. 위 명령은 도구를 돌리기 전에 미리 보고 싶을 때만 쓴다.

inbox가 비어 있으면 **거기서 멈추고** 파일을 넣으라고 안내한다. 빈 묶음을 만들어 두면 뒤 단계가 0으로 나눈다.

## 2. `tools/results_collect.py`를 만든다(없으면)

```
python3 tools/results_collect.py [--dry-run] [--strict]
```

| 단계 | 하는 일 | 실패 처리 |
|---|---|---|
| 읽기 | `data/results/inbox/*.json` 전부 | 파싱 실패 → 그 파일만 건너뛰고 `invalid`에 계상 |
| 판별 | `schema`가 `quiz-export/*`인가 | 아니면 건너뛰고 이유를 남긴다 |
| 검사 | 레코드마다 필수 필드·타입·범위 | 어긴 레코드만 버린다 (파일 전체를 버리지 않는다) |
| 정규화 | 이름의 앞뒤 공백 제거, 가운데 연속 공백을 하나로, 유니코드 NFC | — |
| 중복 | `name + playedAt` | 두 번째부터 버리고 `duplicate`에 계상 |
| 라벨 | 위의 `이름_N` 규칙 | — |
| 쓰기 | `results.json` | `--dry-run`이면 쓰지 않는다 |

**누적이 원칙이다.** 이미 `results.json`이 있으면 **먼저 싣고** `inbox/`의 것을 얹는다. `inbox/`는 "이번에 새로 온 것"만 담는 자리이고, 반영이 끝나면 `/teacher-mode` 6단계가 `archive/`로 옮긴다. 그래서 매번 전체를 다시 모으지 않아도 되고, 옮기지 않으면 다음 실행에서 같은 파일을 또 읽어 "추가 0건"만 늘어난다.

> 이름 정규화는 **표기를 다듬는 데까지만**이다. 공백·NFC·전각 정도로, 같은 문자열을 같게 보이게 하는 일만 한다. `철민`을 `김철민`으로 미루어 짐작하는 일은 하지 않는다.

레코드 검사 규칙(어기면 그 레코드를 버린다):

- `name`이 비어 있지 않은 문자열
- `playedAt`이 파싱 가능한 ISO 시각
- `score`가 정수, `correctCount`·`total`이 0 이상 정수이고 `correctCount <= total`
- `total`이 0보다 큼 (0이면 정답률 계산이 0으로 나뉜다)
- `accuracy`가 `0..1`. **`1`을 넘으면 백분율로 들어온 것이라 보고 100으로 나눠 고친다**(경고 1건). 그래도 범위를 벗어나면 `correctCount/total`로 다시 계산한다
- `byCategory`의 값이 전부 `0..1`
- `responses`가 있으면 배열이고 각 항목에 `id`·`correct`가 있음. **`responses.length`가 `total`과 다르면 경고**하되 버리지는 않는다(중간에 새로고침한 판)

`--strict`면 경고도 실패로 쳐서 종료 코드 1을 낸다.

**중복을 건너뛰되 조용히 넘기지 않는다.** 파일별로 `records / added / duplicate / invalid`를 표로 낸다. `added`가 0인 파일이 있으면 눈에 띄어야 한다 — 옛날 파일을 다시 낸 것이다.

## 3. 시험용 결과 파일이 필요하다면

이 환경에는 브라우저가 없어 실제로 게임을 쳐서 결과 파일을 만들 수 없다. 파이프라인만 시험하려면 가짜 파일을 만든다.

```bash
cd quiz-game && python3 tools/make_test_results.py
```

`TEST-`로 시작하는 파일 9개가 `inbox/`에 생긴다. 수집기가 걸러야 할 것을 일부러 다 심어 둔다 — **같은 판이 두 파일에 중복**, 앞뒤 공백이 다른 이름, **구버전(`responses` 없음) 기록**, `accuracy`가 백분율로 든 레코드, `total: 0`, `correctCount > total`, 빈 이름, 깨진 JSON, 다른 앱의 파일. 4단계 문항 분석까지 시험하려면 `--bulk`를 함께 준다.

**가짜 데이터는 `testData: true`를 달고 나온다.** 실제 결과와 섞지 말 것.

## 4. 돌린다

```bash
cd quiz-game && python3 tools/results_collect.py $ARGUMENTS
```

`--dry-run`을 먼저 돌려 무엇이 들어오고 무엇이 버려지는지 본 뒤 반영하는 것을 권한다. **`results.json`이 이미 있으면 덮어쓰기 전에 `data/results/archive/results-<타임스탬프>.json`으로 옮겨 둔다.**

## 5. 검증

```bash
cd quiz-game && python3 - <<'PY'
import json, sys, os, re, unicodedata
from collections import Counter
p = 'data/results/results.json'
if not os.path.exists(p):
    print('❌ results.json이 없다'); sys.exit(1)
d = json.load(open(p, encoding='utf-8'))
ok = True
def chk(l, c, h=''):
    global ok; print(('  ✅ ' if c else '  ❌ ') + l + ('' if c else '  ← ' + h)); ok = ok and c

chk('스키마 표기', d.get('schema') == 'quiz-results/v1')
recs, parts = d.get('records', []), d.get('participants', [])
chk('레코드 있음', len(recs) > 0, '빈 묶음으로는 통계를 낼 수 없다')
chk('참가자 = 판 수 (1:1)', len(parts) == len(recs),
    f'참가자 {len(parts)} vs 판 {len(recs)} — 같은 이름을 합쳤을 수 있다')

keys = [(r['name'], r['playedAt']) for r in recs]
dup = [k for k, n in Counter(keys).items() if n > 1]
chk('중복 판 없음 (name+playedAt)', not dup, f'{dup[:3]}')

labels = [r['label'] for r in recs]
chk('라벨 유일', len(set(labels)) == len(labels),
    f'{[l for l, n in Counter(labels).items() if n > 1][:3]}')

# 라벨 규칙 재현: 1회는 접미사 없음, 2회 이상은 playedAt 순으로 _1..
byname = {}
for r in recs: byname.setdefault(r['name'], []).append(r)
exp = {}
for n, rs in byname.items():
    rs = sorted(rs, key=lambda r: r['playedAt'])
    for i, r in enumerate(rs, 1):
        exp[(r['name'], r['playedAt'])] = n if len(rs) == 1 else f'{n}_{i}'
bad = [r['label'] for r in recs
       if not r['label'].startswith(exp[(r['name'], r['playedAt'])])]
chk('라벨 규칙 일치 (1회는 그대로, 2회+는 playedAt 순 _1..)', not bad, f'{bad[:3]}')

ids = {q['id'] for q in parts}
orphan = [r['label'] for r in recs if r.get('participantId') not in ids]
chk('모든 레코드가 참가자에 연결됨', not orphan, f'{orphan[:3]}')

bad2 = [r['label'] for r in recs
        if not (0 <= r.get('accuracy', -1) <= 1) or r.get('total', 0) <= 0
        or r.get('correctCount', 0) > r.get('total', 0)]
chk('수치 범위 정상', not bad2, f'{bad2[:3]}')

v2 = sum(1 for r in recs if isinstance(r.get('responses'), list))
v1 = sum(1 for r in recs if r.get('responses') is None)
chk('v1/v2 구분 보존', v1 + v2 == len(recs), 'responses가 [] 로 뭉개졌을 수 있다')

multi = {n: len(rs) for n, rs in byname.items() if len(rs) > 1}
print(f'\n참가자 {len(parts)}명 (문항분석 가능 {v2}판, 총점만 {v1}판)')
print(f'서로 다른 이름 {len(byname)}개' + (f' / 여러 번 친 이름 {len(multi)}개: '
      + ', '.join(f'{k}×{v}' for k, v in list(multi.items())[:5]) if multi else ''))
if v2 == 0:
    print('⚠️  문항별 분석이 불가능하다 — 전부 구버전 기록이다.')
    print('   /teacher-export를 적용한 뒤 새로 받은 결과여야 responses가 담긴다.')
for w in d.get('warnings', [])[:10]: print('  ⚠️ ', w)
sys.exit(0 if ok else 1)
PY
```

## 6. 보고

- 파일별 `records / added / duplicate / invalid` 표
- 참가자 수 · 서로 다른 이름 수 · **여러 번 친 이름과 횟수**
- v2 비율 (문항 분석 가능한 판이 몇이나 되나)
- 버린 레코드와 그 이유

---

## 다음

**`/teacher-stats`**가 이 `results.json`을 읽어 통계를 낸다.
