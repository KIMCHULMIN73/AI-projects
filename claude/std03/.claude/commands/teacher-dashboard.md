---
description: analysis.json을 읽어 선생님 대시보드 화면(teacher.html)을 만들고 띄운다 (선생님 모드 5단계)
argument-hint: "[--open 서버까지 띄우기 | --rebuild 화면 다시 만들기 | --verify 검증만]"
allowed-tools: Bash, Read, Grep, Write, Edit
---

실행 인수: **$ARGUMENTS**

> 이 하네스는 위치 인수(`$1`)를 신뢰할 수 없다 — 0-based로 밀려 들어온다.
> **`$ARGUMENTS` 한 덩어리만 쓰고 옵션은 그 문자열에서 골라낼 것.**

선생님이 실제로 들여다보는 화면이다. **한 화면에서 전체를 보고, 클릭 한 번으로 한 사람으로 파고든다.**

## 이 화면의 유일한 규칙 — 계산하지 않는다

`data/results/analysis.json`을 `fetch`해서 **그리기만** 한다. 평균도 석차도 변별도도 여기서 구하지 않는다.

기존 모듈2가 지키는 규율과 같다 — CLAUDE.md는 `views.js`에 대해 **"뷰에 로직이 새어 들어가면 이 분리가 무너진다"**고 못박아 뒀다. 여기서는 더 위험하다. **화면이 평균을 다시 구하면 터미널과 화면이 서로 다른 1등을 말할 수 있다.**

| 해도 되는 것 | 하면 안 되는 것 |
|---|---|
| 정렬 (열 머리 클릭) | 평균·표준편차·z·백분위 계산 |
| 필터 (분야·검색) | 석차 매기기 |
| 소수점 자리·백분율 표기 변환 | 정답률을 `correctCount/total`로 다시 구하기 |
| `null`을 `—`로 그리기 | `null`을 0으로 채워 그리기 |
| 색·막대 길이 매핑 | 이상치·플래그 판정 |

숫자 하나라도 화면에서 새로 만들어야 할 것 같으면, 그건 **`/teacher-stats`나 `/teacher-compare`에 그 값을 추가할 신호**다.

## 왜 파일을 드래그해서 바로 보게 하지 않는가

결과 파일을 화면에 끌어다 놓으면 편하다. 하지만 그러려면 **병합·중복 제거·통계를 JS로 다시 짜야 한다.** 그 순간 같은 로직이 두 벌이 되고, 이 리포지토리는 그 대가를 이미 안다(`validate_bank.py` ↔ `storage.js` — "자동으로 동기화되지 않는다").

그래서 **파일 선택기는 두되 받는 것은 `analysis.json` 하나뿐**이다. 다른 묶음을 열어 볼 때 쓴다. 원본 결과 파일을 떨구면 "이 파일은 `/teacher-collect`로 넣으세요"라고 안내만 한다.

---

## 1. 만들 파일

```
quiz-game/
  teacher.html            # 대시보드 (게임과 별개의 페이지)
  css/teacher.css         # 대시보드 전용 스타일
  js/teacher/
    dashboard-views.js    # 순수 렌더 함수 (이벤트 바인딩 금지)
    dashboard-main.js     # fetch + 배선
```

`index.html`·`style.css`·`views.js`는 **한 줄도 고치지 않는다.** 게임과 대시보드는 서로를 모른다. 선생님 모드를 통째로 지워도 게임은 그대로 돌아야 한다(`preview.html`·`tools/`와 같은 규율).

**`views.js`를 재사용하지 않는 이유**: 저건 게임 화면 4개의 `#id`에 묶여 있어(`el.rankingBody` 등) 대시보드에서 부르면 없는 요소를 찾는다. 억지로 공유하면 게임 쪽 마크업을 대시보드가 인질로 잡는다.

## 2. 화면 구성

### 상단 — 요약 띠

`참가 N명 · 평균 μ · 표준편차 σ · 중앙값 · 최고/최저`

그 옆에 **작은 글씨 두 가지를 반드시 적는다.**

- `참가 단위: 판 1회 = 1명 (같은 이름도 따로 셈)` — 이걸 밝히지 않으면 `김철민_1`·`김철민_2`를 보고 사람 수를 잘못 읽는다
- `생성: 2026-09-01 12:35` — `generatedAt`. 데이터가 언제 것인지 모르면 지난주 표를 오늘 것으로 읽는다

### 1) 순위표 (기본 화면)

| 석차 | 라벨 | 점수 | 정답률 | z | 백분위 | 한국사 | 과학 | 지리 | 예술 |

- 열 머리 클릭으로 정렬, 라벨 검색 상자
- **z와 백분위가 이 화면의 핵심이다.** 점수만으로는 위치를 모른다 — 340점이 잘한 건지 못한 건지는 평균과 표준편차를 봐야 안다
- 분야 4칸은 숫자와 함께 **가로 막대**로 — 30줄 표에서 숫자만 있으면 강약이 안 보인다
- 평균보다 낮은 칸은 옅은 붉은 기, 높은 칸은 옅은 푸른 기. **색만으로 뜻을 전하지 말고 숫자를 항상 함께 둔다**(색각 이상 대응)
- `null`은 `—`, 마우스를 올리면 사유(`analysis.json`의 `notes`)

### 2) 점수 분포 — 본인 위치를 여기서 본다

`overall.histogram`을 가로 막대로 그리고, 평균 자리에 세로선, **±1σ 구간을 옅게 칠한다.** 순위표에서 한 줄을 고르면 **그 사람의 점수 위치에 표식이 선다.** "평균에서 얼마나 떨어져 있나"가 숫자(z)와 그림 양쪽으로 보이게 하는 것이 이 화면의 목적이다.

**구간은 파일에 있는 것을 그대로 쓴다**(화면이 다시 나누지 않는다).

### 3) 분야별 프로파일

분야 4개의 평균 정답률 + 표준편차. 가장 약한 분야를 눈에 띄게.

### 4) 난이도 검증 줄

난이도 5개의 실제 정답률을 `최하 → 최상` 순으로. **왼쪽이 높고 오른쪽이 낮은 계단이면 정상**이고, 흐트러진 자리를 붉게 표시한다. 라벨이 체감과 어긋난다는 뜻이다(CLAUDE.md 사후 수정 §6에서 실제로 겪은 일).

### 5) 참가자 상세 (행 클릭)

분야 4 + 난이도 5 정답률, 전체 평균 겹쳐 그리기, 강점/약점, 평균 소요시간, 그리고 분포에서의 자기 위치.

### 6) 재검토 후보 문항

`items`의 플래그가 붙은 것만. 🔴(`answer-suspect`·`negative-D`) 먼저. id · 문항 앞머리 · `n` · `p` · `D` · 선택지 분포 막대. 표 위에 **"출제 380문항 중 판정 가능 41개"**를 크게 — 이 줄이 없으면 표가 실제보다 정밀해 보인다.

---

## 3. 구현 규칙

**모든 텍스트는 `textContent`로 넣는다.** 화면에 뜨는 라벨은 사람이 게임 시작 화면에 직접 타이핑한 문자열에서 나왔다. `innerHTML`을 쓰면 이름에 넣은 태그가 그대로 실행된다. `views.js`가 같은 이유로 지키는 규칙이고, **여기서는 남의 입력이 선생님 화면에서 도는 것**이라 더 엄하다.

> 아래 검증은 **문자열만 본다** — 주석에 `innerHTML`이라고 적어 두기만 해도 걸린다(실제로 "innerHTML을 쓰면 안 된다"고 쓴 주석 때문에 한 번 떨어졌다). 그물이 일부러 단순한 것이니 주석 쪽을 고칠 것. `등급`·`학급`·`성장`·`@media print` 검사도 마찬가지다.

- `dashboard-views.js`는 **이벤트를 바인딩하지 않는다.** 행에 `data-participant-id`만 달고, 배선은 `dashboard-main.js`가 위임으로 한 번만 건다(`views.js`/`main.js` 관계 그대로)
- 라이브러리 없음. 차트는 `div` 폭 %와 인라인 SVG로 그린다(빌드 도구도 npm도 없는 리포지토리 관례)
- 분야 라벨은 하드코딩하지 말고 **`data/questions.js`의 `CATEGORIES`에서 가져온다.** 화면 라벨의 유일한 출처다
- `analysis.json`이 없으면 **깨지지 말고** "먼저 `/teacher-collect` → `/teacher-stats`를 돌리세요"를 화면에 띄운다
- `schema`가 `quiz-analysis/v1`이 아니면 경고를 띄우고 그린다. 조용히 잘못 그리는 것보다 낫다
- **`--anon`으로 만든 파일이면 상단에 `가명 표시 중` 배지**를 띄운다. 가명인 줄 모르고 `참가자A`를 찾아다니는 일이 없게

**`file://`로 열면 동작하지 않는다.** `fetch`가 CORS로 막혀 `analysis.json`을 못 읽는다. `index.html`이 쓰는 것과 같은 방식으로 **일반 `<script>`**(module 아님) 감지 안내를 넣는다 — module 스크립트는 바로 그 상황에서 실행 자체가 안 되므로 반드시 일반 script여야 한다.

## 4. 띄운다

`run.sh`는 `quiz-game/`을 통째로 서빙하므로 **손댈 것이 없다.** 주소만 다르다.

```bash
cd quiz-game && NO_OPEN=1 ./run.sh &
sleep 2 && curl -s -o /dev/null -w "teacher.html %{http_code}\n" http://127.0.0.1:8000/teacher.html
```

`--open`을 주면 `./run.sh`를 그냥 띄워 브라우저까지 연다(그래픽 환경이 있을 때만).

## 5. 검증

```bash
cd quiz-game && python3 - <<'PY'
import re, sys, os
ok = True
def chk(l, c, h=''):
    global ok; print(('  ✅ ' if c else '  ❌ ') + l + ('' if c else '  ← ' + h)); ok = ok and c

for f in ('teacher.html', 'css/teacher.css',
          'js/teacher/dashboard-views.js', 'js/teacher/dashboard-main.js'):
    chk(f'{f} 존재', os.path.exists(f))
if not ok: sys.exit(1)

html = open('teacher.html', encoding='utf-8').read()
V = open('js/teacher/dashboard-views.js', encoding='utf-8').read()
M = open('js/teacher/dashboard-main.js', encoding='utf-8').read()

print('── 계산 금지 ──')
chk('뷰가 통계를 다시 구하지 않는다',
    not re.search(r'Math\.sqrt|\.reduce\([^)]*\+[^)]*\)\s*/\s*', V),
    '평균/표준편차를 화면에서 계산한 흔적')
chk('정답률을 다시 나누지 않는다', not re.search(r'correctCount\s*/\s*', V + M))

print('── 안전 ──')
chk('innerHTML 미사용 (사람이 친 이름이 들어온다)',
    'innerHTML' not in V and 'innerHTML' not in M, 'textContent를 쓸 것')
chk('뷰는 이벤트를 바인딩하지 않는다', 'addEventListener' not in V,
    '배선은 dashboard-main.js가 독점한다')

print('── 계약 ──')
chk('analysis.json을 읽는다', 'analysis.json' in M)
chk('스키마 확인', 'quiz-analysis/v1' in M)
chk('CATEGORIES를 가져다 쓴다', 'CATEGORIES' in V + M, '분야 라벨을 하드코딩하지 말 것')
chk('null을 0으로 채우지 않는다', not re.search(r'\?\?\s*0\b|\|\|\s*0\b', V),
    '못 구한 값은 —로 그린다')
chk('참가 단위를 화면에 밝힌다', re.search(r'참가\s*단위|1회\s*=\s*1', html + V) is not None,
    '같은 이름도 따로 센다는 사실을 적어야 한다')

print('── 걷어낸 것이 다시 들어오지 않았나 ──')
for w, why in (('등급', '제도적 등급은 두지 않는다'),
               ('@media print', '인쇄 레이아웃은 두지 않는다'),
               ('학급', '묶음은 하나뿐이라 학급 개념이 없다'),
               ('성장', '판 하나가 참가자 하나라 성장은 없다')):
    chk(f'{w} 없음', w not in html + V + M + open('css/teacher.css', encoding='utf-8').read(), why)

print('── 게임 무개입 ──')
for f in ('index.html', 'css/style.css', 'js/views.js', 'js/main.js'):
    s = open(f, encoding='utf-8').read()
    chk(f'{f}가 대시보드를 모른다', 'teacher.html' not in s and 'dashboard' not in s)

print('── file:// 안내 ──')
tail = html[html.rfind('<script'):]
chk('감지 스크립트 있음', 'file:' in html)
chk('감지 스크립트가 module이 아님', 'type="module"' not in tail,
    'module은 file://에서 실행되지 않아 안내가 영원히 안 뜬다')
sys.exit(0 if ok else 1)
PY
```

브라우저에서 눈으로 확인할 것:

- [ ] `http://127.0.0.1:<포트>/teacher.html`이 열리고 요약 띠가 보인다
- [ ] 상단에 **참가 단위**와 **생성 시각**이 적혀 있다
- [ ] 같은 이름을 여러 번 친 경우 `김철민_1`·`김철민_2`가 **각각 한 줄**로 보인다
- [ ] 열 머리를 눌러 정렬되고, 라벨 검색이 된다
- [ ] 한 줄을 고르면 분포 그림에 **그 사람 위치 표식**이 서고 상세가 펼쳐진다
- [ ] `null` 자리가 `0`이 아니라 `—`로 보인다
- [ ] 난이도 줄이 최하→최상으로 내려가는 계단이다 (아니면 붉게 표시된다)
- [ ] 재검토 후보 표 위에 판정 가능 문항 수가 적혀 있다
- [ ] 콘솔에 에러가 없다
- [ ] `index.html`(게임)이 예전과 똑같이 동작한다

---

## 다음

다섯 단계를 한 줄로 돌리려면 **`/teacher-mode`**.
