---
description: 지정한 카테고리(없으면 전체)의 문항에서 모호한 최상급 표현을 찾아 명시할 기준을 제안한다
argument-hint: "[카테고리 — 한국사 | 과학 | 세계지리 | 예술 (비우면 전체)]"
allowed-tools: Bash, Read, Grep, WebSearch, WebFetch
---

검증 대상 카테고리: **$ARGUMENTS**

비어 있으면 400문항 전체를, 카테고리를 지정하면 그 분야 100문항만 검증한다.

최상급 표현은 **기준이 없으면 정답이 둘 이상이 되는** 가장 흔한 원인이다. CLAUDE.md 「퀴즈 문제 교차 검증 가이드라인」 2번이 이걸 다룬다.

**찾는 데서 끝내지 말 것.** 모호한 표현을 찾았으면 **어떤 기준을 명시해야 하는지**까지 구체적으로 제시해야 한다.

## 카테고리 이름

아래 중 아무 표기나 받는다. 인식하지 못하는 값이면 스크립트가 알려 주니, 그때는 사용자에게 되묻고 멈출 것.

| 분야 | 받는 표기 |
|---|---|
| 한국사 | `한국사` `korean-history` `korean_history` `KH` |
| 과학 | `과학` `science` `SC` |
| 세계지리 | `세계지리` `지리` `world-geography` `world_geography` `WG` |
| 예술·문화·시사 | `예술` `문화` `arts-culture` `arts_culture` `AC` |

문항 id(`SC-012`)를 주면 그 문항만 본다. 파일에 없는 **새 문항 텍스트를 붙여넣으면** 스크립트를 건너뛰고 눈으로 읽어 곧장 2단계로 간다 — 은행에 넣기 전에 거르는 것이 이 명령어의 주 용도다.

## 1. 찾기

```bash
cd quiz-game && python3 - "$ARGUMENTS" <<'PY'
import re, glob, sys
from collections import defaultdict

OBJ = re.compile(r"\{\s*\n\s*id:\s*'([^']+)',(.*?)\n  \},", re.S)
Q   = re.compile(r"\n\s*question:\s*(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')")
CH  = re.compile(r"\n\s*choices:\s*\[(.*?)\],", re.S)
STR = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'")
ANS = re.compile(r"answerIndex:\s*(\d)")

# ── 모호할 수 있는 표현 ──────────────────────────────────────────
PATS = {
    '가장':          r'가장',
    '최초/처음':     r'최초|처음|맨 처음',
    '최대/최소':     r'최대|최소',
    '최고/최저':     r'최고|최저',
    '최장/최단/최다': r'최장|최단|최다',
    '유일':          r'유일',
    '제일':          r'제일',
    '대표적':        r'대표적|손꼽|꼽히는',
}

# ── 무엇에 대한 최상급이냐에 따라 명시해야 할 기준 ────────────────
#    (주제어 정규식, 후보 기준, 갈림길이 되는 사례)
CRITERIA = [
    (r'산|봉',        '해발고도 / 산기슭부터의 높이 / 지구 중심에서의 거리',
                      '해발이면 에베레스트, 산기슭부터면 마우나케아, 지구 중심 기준이면 침보라소'),
    (r'호수',         '면적 / 저수량 / 수심 / 담수·염호 구분',
                      '면적이면 카스피해, 담수호 면적이면 슈피리어호, 수심·저수량이면 바이칼호'),
    (r'강|하천',      '길이 / 유량 / 유역 면적 + 발원지를 어디로 잡는지',
                      '길이는 나일강이 통설이나 발원지에 따라 아마존이 길다는 주장도 있다'),
    (r'섬',           '면적 / 인구',
                      '면적이면 그린란드, 인구면 자바섬. 호주는 대륙으로 분류'),
    (r'바다|해양|대양', '면적 / 부피 / 평균수심',
                      '태평양이 셋 다 1위라 대개 문제되지 않는다'),
    (r'나라|국가',    '국토 면적 / 인구 / 경제 규모 + 국제적 승인 여부 + 시점',
                      '면적이면 러시아, 인구면 2023년 이후 인도, 최소 면적이면 바티칸'),
    (r'대륙',         '면적 / 인구 / 기온',
                      '"넓은"은 면적, "추운"은 연평균 기온으로 못 박을 것'),
    (r'사막',         '면적 / 건조도(연평균 강수량) / 더운 사막인지 여부',
                      '면적이면 남극, 더운 사막 면적이면 사하라, 건조도면 아타카마'),
    (r'폭포',         '낙차 / 유량 / 폭',
                      '낙차면 엔젤 폭포, 유량이면 다른 답이 된다'),
    (r'반도|삼각주|산맥', '면적 / 길이 + 육상인지 해저까지 포함인지',
                      '산맥 길이는 육상이면 안데스, 해저 중앙해령까지면 대서양 중앙해령'),
    (r'기관|장기',    '무게 / 겉넓이 / 부피',
                      '무게면 피부, 흡수 면적이면 소장(융모 포함 약 30㎡)'),
    (r'행성|천체|별', '지름 / 질량 / 거리 + 항성·행성·위성 구분',
                      '"가장 가까운 별"은 항성인지 천체인지에 따라 태양/달로 갈린다'),
    (r'원소|기체',    '어디에서의 비율인지(지각·대기·우주·인체) + 질량비인지 부피비인지',
                      '지각이면 산소, 우주면 수소, 대기면 질소'),
    (r'상|시상식|영화제', '권위는 주관적이므로 "대표하는" 등으로 바꾸거나 분야·국가를 한정',
                      '"가장 권위 있는"은 근거를 댈 수 없다'),
    (r'우승|기록|수상', '집계 시점 — 대회가 열릴 때마다 값이 바뀐다',
                      '월드컵 최다 우승은 대회가 끝날 때마다 재확인해야 한다'),
    (r'최초|처음',    '무엇을 "처음"으로 볼지(전래/공인, 시범/정식, 설립/통합) + 지역 범위',
                      '임시정부는 연해주 3/17 → 상하이 4/11 → 한성 4/23 순으로 셋이 섰다'),
]

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

raw = (sys.argv[1] if len(sys.argv) > 1 else '').strip()
qid_only = raw.upper() if re.fullmatch(r'(KH|SC|WG|AC)-\d{3}', raw.upper()) else None
stem = None
if raw and not qid_only:
    stem = ALIAS.get(raw.lower())
    if not stem:
        print(f"카테고리를 알 수 없습니다: {raw!r}")
        print("받는 값: 한국사 | 과학 | 세계지리 | 예술  (또는 korean-history / science / world-geography / arts-culture)")
        sys.exit(1)

print(f"검증 대상: {CATNAME[stem] if stem else (qid_only or '전체 400문항')}\n")

hits, total = defaultdict(list), 0
for path in sorted(glob.glob('data/questions/*.js')):
    s = path.split('/')[-1][:-3]
    if stem and s != stem:
        continue
    src = open(path, encoding='utf-8').read()
    for m in OBJ.finditer(src):
        if qid_only and m.group(1) != qid_only:
            continue
        total += 1
        text = Q.search(m.group(2)).group(1)[1:-1]
        tags = [n for n, p in PATS.items() if re.search(p, text)]
        if not tags:
            continue
        choices = [t[1:-1] for t in STR.findall(CH.search(m.group(2)).group(1))]
        ai = int(ANS.search(m.group(2)).group(1))
        advice = [(c, why) for pat, c, why in CRITERIA if re.search(pat, text)]
        hits[CATNAME[s]].append((m.group(1), tags, text, choices, ai, advice))

n = sum(len(v) for v in hits.values())
print(f"{total}문항 중 모호할 수 있는 표현 포함: {n}문항\n")
for cat, rows in hits.items():
    print(f"{'='*70}\n{cat} — {len(rows)}건\n{'='*70}")
    for qid, tags, text, choices, ai, advice in rows:
        print(f"\n[{qid}] {text}")
        print(f"  표현 : {', '.join(tags)}")
        print(f"  보기 : " + " / ".join(f"*{c}*" if i == ai else c for i, c in enumerate(choices)))
        if advice:
            for crit, why in advice:
                print(f"  기준 : {crit}")
                print(f"         ↳ {why}")
        else:
            print(f"  기준 : (주제어 미분류 — 무엇을 재는 잣대인지 직접 판단할 것)")
    print()
PY
```

`기준` 줄은 **후보를 늘어놓은 것일 뿐 판정이 아니다.** 그중 무엇을 명시해야 하는지는 다음 단계에서 보기와 함께 보고 정한다.

## 2. 판정

최상급이 있다는 것 자체는 결함이 아니다 — **기준이 없고 보기에 다른 잣대의 정답이 섞여 있는 것**이 결함이다.

| 판정 | 뜻 | 조치 |
|---|---|---|
| ✅ 기준 있음 | 문제에 잣대가 적혀 있다 | 없음 |
| ⚠️ 기준 없음 | 잣대가 없지만 보기에 경쟁자가 없어 실무상 답은 하나 | 기준 보완 권장 |
| 🔴 정답 충돌 | 다른 잣대로 읽으면 **보기 중 다른 것이 답이 된다** | 반드시 수정 |

**⚠️와 🔴은 보기 4개를 봐야 갈린다.** 문제 텍스트만 보고 판정하지 말 것. 1단계가 뽑아 준 `기준` 후보를 하나씩 대입해 보고, **그 잣대로 답이 되는 보기가 실제로 목록에 있는지** 확인해야 🔴인지 알 수 있다.

수치가 걸린 최상급(높이·넓이·깊이·인구·우승 횟수)은 **값이 낡았을 수 있으므로 검색으로 확인**할 것.

### 오탐으로 걸러야 하는 것

- **역사 용어의 일부** — `최고 권력 기구`(교정도감), `최고 회의 기구`(도병마사), `최고 관직`(상대등), `최고 기구`(정당성)는 최상급이 아니라 그 기구의 정의다.
- **단어에 우연히 섞인 글자** — `가장자리`(사헬), `최초의 근대적 조약인 강화도 조약의 **상대국**`처럼 최상급이 정답이 아니라 대상을 지목하는 수식어인 경우.

## 3. 기준을 명시하는 법

⚠️·🔴로 판정한 문항마다 **고쳐 쓴 문장을 직접 제시할 것.** "기준을 명시하세요"라고 지시만 하는 것은 개선안이 아니다.

방법은 넷이고 **위에서부터 시도한다** — 아래로 갈수록 파급이 크다.

| 방법 | 언제 | 예 |
|---|---|---|
| ① 잣대를 넣는다 | 무엇으로 재는지만 밝히면 답이 하나가 될 때 | `세계에서 가장 높은 산` → `**해발고도가** 세계에서 가장 높은 산` |
| ② 범위를 한정한다 | 잣대는 분명한데 대상 집합이 넓을 때 | `임시정부가 처음 수립된 도시` → `**3·1 운동 직후 중국에서 수립된** 임시정부가 처음 자리 잡은 도시` |
| ③ 시점을 박는다 | 값이 시간에 따라 변할 때 | `월드컵에서 가장 많이 우승한 나라` → `**2026년 대회까지 기준으로,** 월드컵에서…` |
| ④ 경쟁 보기를 교체한다 | ①~③으로도 그 보기가 답이 될 수 있을 때 | `가장 무거운 기관` 보기에서 **소장 → 뇌** |

주관적 잣대(`가장 권위 있는`, `가장 유명한`)는 근거를 댈 수 없으므로 **①이 아니라 표현 자체를 바꾼다** — `미국 대중음악 분야를 대표하는 시상식은?`

정답 자체를 바꾸는 방향은 **마지막 수단**이다. 난이도 배치와 해설까지 함께 흔들린다.

해설도 함께 손볼 것. 좋은 해설은 **다른 잣대로는 무엇이 답이 되는지**를 한 줄 담는다 — `빅토리아호는 세 나라에 걸쳐 있다. 물의 양과 수심으로는 탕가니카호가 아프리카에서 가장 크다.`

## 4. 보고 형식

```
🔴 SC-012  사람의 몸에서 가장 넓은 면적을 차지하는 기관은?
   충돌   면적 기준이면 소장(융모 포함 약 30㎡)이 피부(약 2㎡)보다 넓다. 보기에 소장이 있다.
   기준   무게 / 겉넓이 / 부피 중 무엇인지 밝혀야 한다
   개선   질문을 "가장 무거운 기관"으로 바꾸고(①), 보기의 소장을 뇌로 교체(④)
   해설   "피부는 약 4kg으로 가장 무겁다. 몸속 장기 중에서는 간이 약 1.5kg으로 가장 무겁다."
```

- 🔴 → ⚠️ 순으로, 심각한 것부터 낸다.
- ✅는 **개수만** 밝히고 나열하지 않는다.
- 오탐은 오탐이라고 밝히고 왜 그런지 한 줄 덧붙인다.
- 카테고리를 지정해 돌렸으면 **그 카테고리만 검사했다는 사실**을 보고 첫머리에 밝힐 것.

## 5. 고친 뒤

수정은 **사용자 승인을 받고** 한다. 승인 없이 파일을 고치지 말 것.

```bash
cd quiz-game && python3 tools/validate_bank.py
```

문제·해설 문구만 고쳤다면 정답 위치는 그대로다. **보기를 교체하거나 순서를 바꿨다면** 정답 위치 쏠림을 다시 셀 것:

```bash
cd quiz-game && python3 -c "
import re,glob
from collections import Counter
c=Counter()
for f in glob.glob('data/questions/*.js'):
    c.update(re.findall(r'answerIndex:\s*(\d)', open(f,encoding='utf-8').read()))
print(dict(sorted(c.items())))
"
```

여러 문항을 손봤다면 발견과 조치를 `CLAUDE.md`의 "사후 수정 이력"에 남긴다.
