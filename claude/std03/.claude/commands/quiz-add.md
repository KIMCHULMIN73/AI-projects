---
description: 지정한 분야에 지정한 난이도의 새 문항을 지정한 개수만큼 검증하며 추가한다
argument-hint: "<분야> <난이도> <개수>   예: 과학 상 5"
allowed-tools: Bash, Read, Grep, Write, WebSearch, WebFetch
---

추가 요청: **$ARGUMENTS**

> 이 하네스는 위치 인수(`$1`·`$2`)를 신뢰할 수 없다 — 0-based로 밀려 들어온다.
> **`$ARGUMENTS` 한 덩어리만 쓰고 분야·난이도·개수는 그 문자열에서 골라낼 것.**

인수는 `<분야> <난이도> <개수>` 세 개다. 위 요청문을 그대로 쪼개 쓴다.

문항을 쓰는 것 자체는 쉽다. 이 명령이 신경 쓰는 것은 **넣는 과정에서 깨지기 쉬운 세 가지**다.

1. **기존 400문항과 겹치지 않기** — 문장이 달라도 같은 것을 묻고 있으면 중복이다
2. **CLAUDE.md 「퀴즈 문제 교차 검증 가이드라인」 4가지 지키기** — 이 프로젝트가 실제로 데인 자리다
3. **난이도 구간 안에 넣고 id를 다시 매기기** — 파일이 쉬운 것부터 어려운 순으로 정렬돼 있어서, 중간에 끼워 넣으면 **뒤쪽 문항 번호가 통째로 밀린다**

3번은 손으로 하면 반드시 틀리므로 `tools/insert_questions.py`가 맡는다. 1·2번이 이 명령을 부른 사람이 실제로 할 일이다.

## 인수

세 개 모두 필요하다. 빠진 것이 있으면 **되묻고 멈출 것.**

| 인수 | 받는 표기 |
|---|---|
| 분야 | `한국사`·`KH` / `과학`·`SC` / `세계지리`·`지리`·`WG` / `예술`·`문화`·`AC` |
| 난이도 | `최상`·`top` / `상`·`high` / `중`·`mid` / `하`·`low` / `최하`·`bottom` |
| 개수 | 1 이상의 정수 |

**한 번에 10개를 넘기지 말 것.** 개수가 늘수록 출처 확인이 헐거워진다. 20개가 필요하면 두 번 나눠 돌리는 편이 결과가 낫다. 10을 넘게 요청받았으면 그 사실을 알리고 나눠 진행하겠다고 밝힌 뒤 진행한다.

## 0. 무엇이 밀리는지 먼저 본다

```bash
cd quiz-game && python3 - "$ARGUMENTS" <<'PY'
import re, sys
ALIAS = {'한국사':'korean-history','kh':'korean-history','korean-history':'korean-history',
         '과학':'science','sc':'science','science':'science',
         '세계지리':'world-geography','지리':'world-geography','wg':'world-geography',
         'world-geography':'world-geography',
         '예술':'arts-culture','문화':'arts-culture','시사':'arts-culture','ac':'arts-culture',
         'arts-culture':'arts-culture'}
DIF = {'최상':'top','top':'top','상':'high','high':'high','중':'mid','mid':'mid',
       '하':'low','low':'low','최하':'bottom','bottom':'bottom'}
ORDER = ['bottom','low','mid','high','top']
LBL = {'bottom':'최하','low':'하','mid':'중','high':'상','top':'최상'}
args = sys.argv[1].split() if len(sys.argv) > 1 else []
stem = next((ALIAS[a.lower()] for a in args if a.lower() in ALIAS), None)
dif  = next((DIF[a.lower()] for a in args if a.lower() in DIF), None)
if not stem or not dif:
    print("분야/난이도를 알 수 없습니다. 사용자에게 되묻고 멈추세요."); sys.exit(2)
src = open(f'data/questions/{stem}.js', encoding='utf-8').read()
rows = re.findall(r"id: '([^']+)',\n    category: C,\n    difficulty: '(\w+)'", src)
blocks = {}
for i, d in rows:
    blocks.setdefault(d, []).append(i)
print(f"대상 : {stem}  {LBL[dif]}({dif})  현재 {len(blocks.get(dif, []))}문항")
print(f"삽입 : {blocks[dif][-1]} 바로 뒤")
after = [i for d in ORDER[ORDER.index(dif)+1:] for i in blocks.get(d, [])]
print(f"밀림 : {len(after)}개" + (f"  ({after[0]} 이후 전부)" if after else "  (없음 — 마지막 구간)"))
PY
```

밀리는 문항이 있으면 그 사실을 **먼저 사용자에게 알린다.** 번호가 바뀌면 CLAUDE.md의 사후 수정 이력에 적힌 id 참조가 어긋나기 때문이다(스크립트가 어떤 문서의 어떤 id인지 뒤에서 짚어 준다).

## 1. 기존 문항을 훑는다 — 중복 회피의 근거

**삽입 스크립트의 중복 검사는 질문 문자열만 본다.** 표현이 다르면 통과해 버리므로, 진짜 중복 회피는 여기서 눈으로 해야 한다.

```bash
cd quiz-game && python3 - "$ARGUMENTS" <<'PY'
import re, sys, glob
ALIAS = {'한국사':'korean-history','kh':'korean-history','korean-history':'korean-history',
         '과학':'science','sc':'science','science':'science',
         '세계지리':'world-geography','지리':'world-geography','wg':'world-geography',
         'world-geography':'world-geography',
         '예술':'arts-culture','문화':'arts-culture','시사':'arts-culture','ac':'arts-culture',
         'arts-culture':'arts-culture'}
stem = next((ALIAS[a.lower()] for a in sys.argv[1].split() if a.lower() in ALIAS), None)
OBJ = re.compile(r"  \{\n    id: '([^']+)',\n(.*?)\n  \},", re.S)
Q   = re.compile(r"\n    question: (\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')")
A   = re.compile(r"\n    choices: \[(.*?)\],\n", re.S)
S   = re.compile(r"\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)'")
src = open(f'data/questions/{stem}.js', encoding='utf-8').read()
for m in OBJ.finditer(src):
    b = m.group(2)
    d = re.search(r"difficulty: '(\w+)'", b).group(1)
    ch = [x or y for x, y in S.findall(A.search(b).group(1))]
    ans = int(re.search(r"answerIndex: (\d)", b).group(1))
    print(f"{m.group(1)} [{d:6}] {Q.search(b).group(1)[1:-1]}  → {ch[ans]}")
PY
```

이 목록을 **끝까지 읽고** 새 문항을 쓴다. 중복 판정 기준은 문장이 아니라 **묻는 대상**이다.

- ❌ `한글을 창제한 왕은?` vs `훈민정음을 반포한 왕은?` — 표현만 다르고 답도 소재도 같다
- ❌ `첨성대를 만든 나라는?` vs `첨성대는 어느 시대 유물인가?` — 같은 소재를 같은 각도로 묻는다
- ⭕ `세종이 만든 천문 기구는?` — 소재가 겹쳐도 **묻는 각도가 다르면** 다른 문항이다

같은 분야 안에서만 겹치는 것이 아니다. 인물·사건은 분야를 넘나드니, 미심쩍으면 은행 전체를 훑는다.

```bash
cd quiz-game && grep -rn "훈민정음\|장영실" data/questions/ | grep question
```

## 2. 문항을 쓴다

### 난이도 기준

| 코드 | 표기 | 기준 | 정답 배점 |
|---|---|---|---|
| `bottom` | 최하 | 대부분 맞춘다 | +10 |
| `low` | 하 | 알 만한데 실수로 틀릴 수 있다 | +20 |
| `mid` | 중 | 정답률 절반쯤 | +30 |
| `high` | 상 | 이 정도 알면 유식하다는 소리를 듣는다 | +40 |
| `top` | 최상 | 상식이라기엔 다소 어렵다 | +50 |

난이도는 **보기 4개를 놓고** 정해진다. 어려운 것을 물어도 나머지 셋이 터무니없으면 `bottom`이고, 쉬운 것을 물어도 헷갈리는 보기가 섞이면 `mid`가 된다. 요청받은 난이도에 맞추려면 **오답 보기의 그럴듯함**을 조절할 것.

### 반드시 지킬 것 — 교차 검증 가이드라인

CLAUDE.md 「퀴즈 문제 교차 검증 가이드라인」 4가지다. **문항을 쓰면서** 확인한다.

1. **정답이 하나뿐인가** — 다른 해석이 가능하면 조건을 명시한다(`면적 기준`, `인구수 대비`)
2. **최상급 표현에 기준이 있는가** — `가장 큰`·`최초의`를 쓰려면 무엇을 기준으로 한 것인지 밝힌다
3. **시간·범위가 명확한가** — 변할 수 있는 값은 시점을(`2026년 기준`), 한정되는 범위는 그 범위를(`삼국 가운데`) 적는다
4. **사실이 맞는가** — 답이 자명하지 않다면 **WebSearch로 출처 2개 이상 확인.** 논란이 있으면 주류 학설을 따르고 해설에 다른 견해를 한 줄 남긴다

**이 프로젝트가 실제로 데인 자리 넷.** 같은 함정을 다시 밟지 말 것.

- **범위 밖 보기** — `삼국 가운데`라 물으면서 보기에 가야·발해를 넣으면 실질 3지선다가 되고, 아는 사람일수록 흔들린다 (KH-004, KH-032)
- **같은 것을 가리키는 보기 둘** — `이벌찬`과 `각간`은 같은 관등의 다른 이름이라 보기가 실질 셋이었다. **문자열이 다르므로 `validate_bank.py`의 중복 검사를 통과해 버린다** (KH-093)
- **잣대가 갈리는 최상급** — `가장 넓은 면적의 기관`은 피부가 아니라 소장이 답이 된다. 하필 그 소장이 보기에 있었다 (SC-012)
- **다른 잣대의 정답이 보기에 섞임** — 임시정부가 `처음` 선 곳은 시기로 보면 블라디보스토크다. 그게 보기에 있었다 (KH-009)

좋은 해설은 **다른 잣대로는 무엇이 답이 되는지**를 한 줄 담는다.

### 정답 위치와 보기 순서

- **정답을 0~3번에 고르게 흩는다.** 새로 쓰는 문항들 안에서도 한쪽으로 몰리지 않게 하고, **연달아 같은 번호가 오지 않게** 한다. 이 프로젝트는 400문항 중 317개의 정답이 0번이라 **첫 보기만 찍어도 79점**이 나온 적이 있다(CLAUDE.md §7).
- **네 보기가 모두 숫자면(연도·개수·수치) 섞지 말고 오름차순으로 둔다.** 기계적으로 섞었다가 `1945년, 1910년, 1929년, 1919년`이 되어 29문항을 되돌린 적이 있다.
- 보기 넷의 **길이와 결을 비슷하게** 맞춘다. 하나만 유독 길거나 구체적이면 그게 답인 티가 난다.

### 써 넣을 파일

문항을 JSON 배열로 스크래치패드에 쓴다. `id`·`category`·`difficulty`는 **적지 않는다** — 스크립트가 채운다.

```json
[
  {
    "question": "질문 텍스트?",
    "choices": ["보기1", "보기2", "보기3", "보기4"],
    "answerIndex": 2,
    "explanation": "왜 그것이 답인지. 다른 잣대로는 무엇이 답이 되는지도 한 줄."
  }
]
```

**사용자에게 문항을 먼저 보여 주고 승인을 받는다.** 승인 없이 은행에 넣지 말 것 — 되돌리려면 번호가 다시 밀린다.

## 3. 넣는다

```bash
cd quiz-game && python3 tools/insert_questions.py <분야> <난이도> <새문항.json> --dry-run
```

`--dry-run`으로 먼저 확인한다. 스크립트가 하는 일:

- 스키마 검사(보기 4개 / `answerIndex` 0~3 / 한 문항 안 보기 중복 / 질문·해설 존재)
- **은행 400문항 전체와 질문 문자열 대조** — 정규화해서 비교하므로 공백·문장부호 차이는 잡아낸다
- 해당 난이도 구간의 **마지막 문항 뒤**에 삽입
- 파일 전체 **id 재부여**(001부터 순서대로) + 밀린 id 매핑 보고
- 머리말과 구간 주석의 **개수 갱신**
- `validate_bank.py`의 `EXPECTED_BANK_MIX` 해당 분야 행 갱신

문제가 없으면 `--dry-run`을 떼고 다시 돌린다.

> 스크립트가 `⚠ 문서가 예전 id를 가리키게 됩니다`를 내면 **그 문서를 직접 고칠 것.** 다만 `SC-001 ~ SC-100` 같은 **범위 표기는 문항 참조가 아니므로** 손대지 않는다 — 스크립트는 이 둘을 구분하지 못하니 사람이 판단한다.

## 4. 검증한다

```bash
cd quiz-game && python3 tools/validate_bank.py
```

전부 PASS여야 한다. `구성 일치`가 FAIL이면 `EXPECTED_BANK_MIX` 갱신이 빠진 것이다.

이어서 **새로 넣은 구간만** 들여다본다. 정답 위치가 쏠렸는지는 이쪽이 본다.

```
/quiz-range <분야> <새 문항 첫 번호> <새 문항 끝 번호>
```

최상급 표현을 썼다면 그 판정도 받는다.

```
/quiz-validate <분야>
```

## 5. 남긴다

- 문항이 늘면 **한 판에 뽑히는 40문제는 그대로**다(분야별 10문제, 난이도 할당량 1/2/4/2/1). 늘어난 만큼 **재도전 시 문제가 덜 겹칠 뿐**이다. 사용자에게 이 점을 밝힐 것 — 문항을 늘리면 게임이 길어지는 줄 아는 경우가 있다.
- 난이도 비율이 10:20:40:20:10에서 벗어나도 **샘플링은 깨지지 않는다.** 분야마다 `top1/high2/mid4/low2/bottom1`만 채우면 되고 지금은 한참 여유가 있다. 다만 늘린 난이도의 문항이 상대적으로 덜 자주 뽑힌다.
- 문항을 여러 개 넣었거나 id가 밀렸으면 `CLAUDE.md`의 "사후 수정 이력"에 남긴다 — **무엇을 왜 넣었는지, 어떤 id가 어디로 밀렸는지.**

## 보고 형식

```
과학 / 상(high) 3문항 추가 — SC-091 ~ SC-093

SC-091  광합성의 명반응이 일어나는 엽록체 내부 구조는?     → 틸라코이드 막  (정답 2번)
        출처 2건 확인. 보기의 스트로마는 암반응 자리라 잣대를 해설에 밝힘
SC-092  ...

밀린 id : SC-091~SC-100 → SC-094~SC-103  (CLAUDE.md의 SC-100 참조 1건 수정함)
검증    : validate_bank.py 21/21 PASS / 새 구간 정답 위치 1·2·0
```

- 새 문항은 **질문·정답·정답 위치**를 한 줄씩 보인다.
- 사실 확인이 필요했던 문항은 **무엇을 확인했는지** 덧붙인다.
- 밀린 id와 고친 문서를 반드시 밝힌다.
