# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

`std03`은 **"누가누가 잘하나 좌충우돌 퀴즈게임"** — 한 화면에서 진행되는 4지선다 상식 퀴즈 게임이다. 요구사항 전체는 `QUIZ_PRD.md`에 있고, 실제 앱은 `quiz-game/`에 있다.

한국사·과학·세계지리·예술/문화/시사 네 분야에서 각 10문제씩 총 40문제를 풀고, 100점에서 시작해 정답 +10 / 오답 −10으로 점수가 오르내린다. 끝나면 성적 요약과 누적 랭킹을 보여준다.

`QUIZ_PRD.md`가 이 프로젝트의 **사양 원본**이다. 규칙(점수·문항 수·난이도 비율)이나 화면 흐름을 바꿔야 하면 PRD와 이 문서를 함께 고칠 것.

## 실행 방법

**`index.html`을 더블클릭해서 열면 동작하지 않는다.** PRD가 ES Modules를 지정했는데, 브라우저는 `file://`에서 모듈 `import`를 CORS 정책으로 차단하기 때문이다. 이때 `main.js`가 통째로 로드되지 않아 **이벤트가 하나도 바인딩되지 않는다** — 화면은 멀쩡해 보이는데 이름을 넣어도 시작 버튼이 활성화되지 않는 증상으로 나타난다. 그래서 `index.html`에 **module이 아닌 일반 `<script>`로 된 `file://` 감지 안내**를 넣어 뒀다(module 스크립트는 이 상황에서 실행 자체가 안 되므로 반드시 일반 script여야 한다).

```bash
cd quiz-game
./run.sh              # 빈 포트를 찾아 서버를 띄우고 브라우저를 자동으로 연다
./run.sh 8080         # 포트 고정(이미 쓰는 중이면 알려 주고 멈춘다)
NO_OPEN=1 ./run.sh    # 브라우저 자동 실행 없이 서버만
```

- 이 리포지토리 환경에는 **다른 python http.server가 8000번을 이미 쓰고 있을 수 있다.** 그래서 기본 실행은 빈 포트를 자동으로 찾는다. 예전처럼 8000번에 그냥 바인딩하면 파이썬 트레이스백이 그대로 튀어나왔다.
- 외부 노출을 막기 위해 `127.0.0.1`에만 바인딩한다.
- 그래픽 환경(`DISPLAY`/`WAYLAND_DISPLAY`)이 있으면 `xdg-open`으로 브라우저를 자동으로 연다. SSH 등 화면이 없는 환경에서는 자동으로 건너뛴다.

빌드·설치·npm은 없다. 서버는 정적 파일 서빙 용도일 뿐이다.

## 검증 방법

자동화된 테스트 러너는 없다(리포지토리 전체 관례).

### 이 환경의 제약 — 검증 수단이 왜 이렇게 생겼는가

이 라즈베리파이 환경에는 **Node/Deno 같은 JS 런타임도, 조작 가능한 브라우저도, SVG 래스터라이저(rsvg/cairosvg/inkscape)도 없다.** 있는 것은 Python 3.13과 PIL뿐이다. 그래서 검증 도구가 전부 파이썬으로 우회하는 형태를 띤다. 이 제약을 모르면 아래 도구들이 왜 이렇게 만들어졌는지 이해되지 않는다.

### 1. 문제 은행 검증

```bash
cd quiz-game
python3 tools/validate_bank.py
```

`questions.js`를 파싱해 스키마를 검사하고, `storage.js`의 샘플링 알고리즘을 **파이썬으로 이식해** 분포를 500회 시뮬레이션한다. **`storage.js`의 샘플링 로직을 고치면 이 스크립트의 이식본도 같이 고쳐야 한다** — 자동으로 동기화되지 않는다.

### 2. 뷰 단독 확인

`./run.sh` 후 `preview.html`. 더미 데이터로 4개 화면과 정답/오답 팝업, 빈 랭킹, 배경 순환을 버튼 하나로 띄워 본다. 게임 로직 없이 모듈2만 본다.

### 3. 제목 SVG 글자 모양 확인

SVG 래스터라이저가 없어 브라우저 없이는 글자 모양을 볼 수 없다. 필요하면 **생성기의 도형 데이터를 PIL로 직접 그려** 확인한다 — `title-svg-generator.py`를 import 해서 `build()`가 돌려주는 획/구멍 좌표를 같은 규칙(둥근 캡·조인, 구멍은 배경색으로 덮기)으로 렌더하면 된다. 실제로 이 방법으로 `충`의 받침 `ㅇ`이 납작하게 뭉개진 것을 찾아 고쳤다. 새 음절을 추가했을 때 유용하다.

### 4. 브라우저 수동 확인

나머지는 전부 수동이다. 시나리오는 맨 아래 통합 검증 체크리스트를 따른다.

## 아키텍처 — 3개 독립 모듈

PRD §9의 핵심 설계다. 세 모듈은 서로의 내부 구현을 모르고 **인터페이스로만 통신**한다. 이 경계를 지켜야 "문항만 늘리기 / 디자인만 바꾸기 / 규칙만 바꾸기"가 서로를 건드리지 않고 가능해진다.

```
std03/
  QUIZ_PRD.md                      # 사양 원본
  QUIZ_PROMPT.CLAUDE               # PRD를 만들어낸 최초 요청문(원본 의도의 근거)
  CLAUDE.md                        # 이 파일
  quiz-game/
    index.html
    run.sh                         # 로컬 서버 실행 래퍼 (+ 브라우저 자동 열기)
    preview.html                   # [개발용] 모듈2 뷰 단독 검증 페이지
    css/style.css                  # [모듈2]
    data/questions.js              # [모듈1] 문제 은행 DB
    js/storage.js                  # [모듈1] 샘플링 + 랭킹 저장/조회
    js/views.js                    # [모듈2] 순수 렌더 함수
    js/audio.js                    # [모듈3] BGM/효과음
    js/game.js                     # [모듈3] 게임 상태머신
    js/main.js                     # [모듈3] 부팅 & 이벤트 연결
    assets/                        # 배경/BGM/효과음 (없으면 자동 폴백)
      README.md                    # 파일명 규칙과 폴백 판별 규칙
    tools/validate_bank.py         # [개발용] 문제 은행 검증기
    tools/title-svg-generator.py   # [개발용] 제목 SVG 생성기
```

`preview.html`과 `tools/*`는 **앱이 로드하지 않는다** — 셋 다 지워도 게임은 그대로 동작한다.

**의존 방향은 단방향이다: 모듈3 → 모듈1, 모듈3 → 모듈2.** 모듈1과 모듈2는 서로를 모르고, 둘 다 모듈3을 모른다.

- **모듈1이 노출**: `getQuizSet()`, `Storage.saveRecord()`, `Storage.getRankings()`, `CATEGORIES`
- **모듈2가 노출**: `Views.showStart()`, `Views.showQuestion(q, state)`, `Views.showFeedback(...)`, `Views.showResult(summary)`, `Views.showRanking(list)`, `Views.setBackground(category)`
- **모듈3**: 위 둘을 import 해 흐름을 구동

`views.js`는 **그리기만** 한다 — 이벤트를 직접 바인딩하지 않고 버튼에 `data-index`만 부여하며, 실제 바인딩은 `main.js`가 한다. 뷰에 로직이 새어 들어가면 이 분리가 무너진다.

## 확정된 설계 판단 (PRD에 없거나 PRD와 충돌한 지점)

1. **ES Modules 유지 + 로컬 서버.** PRD §0은 "ES Modules"와 "`index.html` 열면 바로 동작"을 동시에 요구하지만 둘은 양립하지 않는다. 모듈 분리가 PRD의 핵심 유지보수 전략이므로 ES Modules를 택하고 `run.sh`를 추가했다. (사용자 확인 완료)
2. **에셋 자동 폴백.** PRD §8은 `assets/`에 배경·BGM·효과음이 미리 배치돼 있다고 전제하지만 실제 파일은 없다. 파일이 있으면 쓰고, 없으면 **분야별 CSS 그라디언트 배경 + Web Audio 합성음**으로 대체한다. 나중에 `assets/`에 파일만 넣으면 코드 수정 없이 그대로 쓰인다. (사용자 확인 완료)
3. **문제 은행 72문항.** PRD는 "분야별 최소 15문항(총 60)"을 요구했고, 재도전 시 문제가 충분히 바뀌도록 분야별 18문항(총 72)으로 채웠다.
4. **제목 글씨체는 VT323이 아니라 SVG 금속 조각.** PRD §0은 "못박힌(도트매트릭스) 글씨체 = VT323"이라고 적었지만, 이는 원본 요청(`QUIZ_PROMPT.CLAUDE`)의 "할일 리스트에서 썼던 못박힌 글씨체"를 잘못 옮긴 것이다. `std02/new version`의 실제 제목은 VT323이 아니라 **나무 널판지 + 못 4개 + 획마다 리벳 구멍이 뚫린 SVG 글자**다. 사용자 확인을 거쳐 std02 방식으로 되돌렸다. VT323은 제목이 아닌 **점수·수치 표시용**으로만 남아 있다.

## 리포지토리 맥락

git 루트는 두 단계 위(`../..`, `AI-projects`)다. 형제 프로젝트(`../std01` 숫자 인식기, `../std02` 할 일 관리 앱)와 공유하는 코드·도구·의존성은 없다. 다만 관례는 이어진다: 프레임워크·빌드 도구 없는 vanilla JS, `localStorage` 영속화, 자동화 테스트 없이 수동 검증, `CLAUDE.md`를 작업 로그로 계속 갱신, 개발 전용 스크립트는 `tools/`에 두고 앱이 로드하지 않게 분리.

---

# 구현 로그

PRD §10의 빌드 순서(모듈1 → 모듈2 → 모듈3)대로 진행한다.

## ✅ 모듈 1 — 데이터 계층 (완료)

UI·사운드 코드 없음. 순수 데이터/로직만.

### `data/questions.js`

- `QUESTION_BANK` — **72문항**. 분야별 18문항, 각 분야 난이도 분포는 `top:2 / high:4 / mid:9 / low:3`.
  - 샘플링 할당량(`top1/high2/mid5/low2`)보다 여유를 둬서 매 게임 문제가 바뀐다.
- `CATEGORIES` — 분야별 `{label, icon}`. **화면 라벨의 유일한 출처**이므로 뷰에서 문자열을 하드코딩하지 말 것.
- `CATEGORY_ORDER` — 출제·표시 순서(한국사→과학→세계지리→예술). `Object.keys` 순서에 의존하지 않으려고 따로 뒀다.
- `DIFFICULTY_ORDER` — `['low','mid','high','top']`. **정렬 순서 자체가 의미를 갖는다** — 인접 난이도 보충 로직이 이 배열의 인덱스 차이를 난이도 거리로 쓴다. 순서를 바꾸면 보충 로직이 깨진다.
- 파일 맨 위에 "새 문항 추가 방법" 5줄 주석.

### `js/storage.js`

- `getQuizSet()` — 분야별 10문제씩 40문제 반환.
  - 난이도 할당량 `{top:1, high:2, mid:5, low:2}`로 샘플링.
  - **부족 시 인접 난이도로 보충**: `nearestAvailable()`이 `DIFFICULTY_ORDER` 인덱스 거리로 가장 가까운 난이도를 고른다. 지금 은행은 여유가 있어 이 경로를 타지 않지만, 문항이 적은 분야를 추가하면 동작한다.
  - 분야 안에서는 셔플(난이도 순서가 드러나지 않게), 분야끼리는 `CATEGORY_ORDER` 순으로 이어 붙임.
- `Storage.saveRecord(record)` / `Storage.getRankings()` / `Storage.clearRankings()`
  - 키는 `quiz.rankings`.
  - 정렬: **점수 내림차순, 동점이면 최신순**(`playedAt` 내림차순).
  - `localStorage` 접근은 전부 try/catch. **저장 실패 시 예외를 밖으로 던지지 않고 `false`를 반환**한다 — 저장 실패로 게임이 멈추면 안 되므로. 읽기 실패나 깨진 JSON은 빈 배열로 복구.
- 파일 하단에 콘솔 테스트 예시 주석.

### 검증 결과 — `python3 tools/validate_bank.py` 18/18 PASS

```
문제 은행 무결성: 72문항, id 중복 없음, 보기 4개, answerIndex 0~3,
                 한 문항 내 보기 중복 없음, category/difficulty 유효,
                 질문·해설 전 문항 존재
난이도 하한:      4개 분야 모두 총 18 / top:2 high:4 mid:9 low:3 → 할당량 충족
샘플링 500회:     항상 40문제 / 세트 내 중복 없음 /
                 분야 블록 순서 유지 / 분야마다 최상1·상2·중5·하2 /
                 재호출 시 세트가 달라짐
```

`storage.js`의 try/catch 견고성(= `localStorage`가 없거나 깨진 JSON일 때 죽지 않음)은 파이썬 이식본이 커버하지 못하므로, 브라우저에서 모듈3 통합 검증 시 함께 확인한다.

## ✅ 모듈 2 — 화면 & 스타일 (완료)

게임 로직·사운드 없음. 정적으로 렌더되는 화면과 스타일만.

### `index.html`

- 화면 4개(`#screen-start` / `#screen-play` / `#screen-result` / `#screen-ranking`)를 두고 **`hidden` 속성**으로 하나만 보인다.
  - `.screen[hidden]`과 `.feedback-overlay[hidden]`에 `display: none !important`를 준 이유: 두 요소 모두 `display: flex/grid`를 갖고 있어서 `hidden` 속성만으로는 안 숨는다.
- 배경은 `<body>`가 아니라 **별도 `#bg-layer` 고정 레이어**가 담당하고, 그 위에 `.bg-scrim`(옅은 어둠)을 깔아 어떤 배경에서도 흰 패널이 또렷하게 뜨도록 했다.
- Google Fonts VT323을 `<link>`로 로드. **제목이 아니라 점수·수치 표시용**이다. 오프라인이면 CSS의 `'Courier New', monospace` 폴백이 쓰인다.
- 맨 아래에 **`file://` 감지 안내 스크립트**가 있다. `type="module"`이 **아닌** 일반 `<script>`여야 한다 — module 스크립트는 바로 그 상황(`file://`)에서 실행 자체가 안 되기 때문이다. 이걸 module로 바꾸면 안내가 영원히 뜨지 않는다.

### `css/style.css`

- 공통 레이아웃: 배경 위 중앙에 `.panel`(둥근 흰 박스), 모든 콘텐츠는 그 안.
- VT323을 쓰는 곳: 상단바 점수, 피드백 팝업 제목/증감, 완료 화면 수치, 랭킹 순위·점수. **메인 제목에는 쓰지 않는다**(아래 제목 항목 참고).
- 피드백 팝업은 정답/오답 애니메이션이 다르다 — 정답은 튀어오르는 `pop-correct`, 오답은 좌우로 흔들리는 `pop-wrong`.
- `prefers-reduced-motion: reduce`에서 모든 애니메이션 정지.
- 720px 이하에서 분야 카드 2열, 보기 1열, 버튼 전폭으로 재배치.

### `js/views.js`

**순수 렌더 함수만.** 이벤트를 직접 바인딩하지 않고 보기 버튼에 `data-index`만 부여한다.

- `showStart()` — 분야 카드를 **`CATEGORIES`를 근거로** 생성한다. 라벨/아이콘을 뷰에 하드코딩하지 않으므로 분야를 늘리면 카드가 자동으로 늘어난다.
- `setBackground(category)` — **그라디언트를 즉시 깔고, `assets/bg/<key>.jpg`가 실제로 있으면 비동기로 덮어쓴다.** `currentBgKey` 가드가 있어 늦게 끝난 이미지 로드가 이미 바뀐 배경을 덮지 않는다. 조사 결과는 `bgProbeCache`에 캐시.
- `showQuestion(q, state)` — `state.index`는 **0-based**이고 화면에는 `index+1 / total`로 표시한다.
- `showFeedback(isCorrect, explanation, options)` — PRD 계약(2인자)에 옵션 인자를 더했다. `{delta, selectedIndex, answerIndex}`로 **보기 버튼에 정답/오답 색을 칠하고 전부 잠근다**. 효과음은 호출하지 않는다(모듈3 담당).
- `showResult(summary)` / `showRanking(list, {highlightIndex})` — 랭킹은 1~3위 메달, 방금 저장된 내 기록은 `.is-me`로 강조. 빈 목록도 처리.
- 계약 외 추가: `preloadBackgrounds()`(F-12), `hideFeedback()`, `bumpScore()`, `focusNameInput()`.
- **모든 텍스트는 `textContent`로 넣는다** — 도전자 이름이 그대로 화면에 들어가므로 `innerHTML`을 쓰면 안 된다.

### 제목: 널판지 + 못 + 리벳 SVG 글씨 (`tools/title-svg-generator.py`)

`std02/new version`의 생성기를 이식한 것으로, **제목 글자를 폰트로 렌더링하지 않고 SVG 도형으로 조립해 그린다.**

- 마크업: `.title-plank`(널판지) 안에 `.nail-tl/tr/bl/br`(못 4개)과 `<h1 class="app-title">`. h1 안의 `TITLE-ART:START/END` 주석 마커 사이는 **생성기 산출물이므로 직접 수정하지 말 것.**
- 널판지·못은 이미지 없이 CSS 그라디언트로만 그린다(나뭇결 `repeating-linear-gradient` 2겹 + 옹이 `radial-gradient` + `inset` 그림자 4겹).
- 획 = `<polyline>`/`<ellipse>` + `stroke-linecap/linejoin: round`, 리벳 구멍 = `<mask id="titleRivets">`의 **검은 원**(흰 원을 덧그리는 게 아니라 실제로 뚫어야 뒤의 나뭇결이 비친다).
- 색은 `stroke="currentColor"` → CSS `.title-art { color: var(--plate-dark) }` 한 줄로 글씨 톤을 바꾼다.
- 2줄 배치: `누가누가 잘하나` / `좌충우돌 퀴즈게임`. viewBox 982×258, 리벳 118개.
- **접근성**: 화면에 텍스트가 없으므로 SVG의 `aria-label`과 `<title>`이 유일한 텍스트 대체물이다(생성기가 `LABEL`에서 자동 생성).

#### 제목 문구를 바꾸려면

```bash
cd quiz-game
# tools/title-svg-generator.py 맨 아래 LINES/LABEL 수정 후
python3 tools/title-svg-generator.py --inject   # index.html의 마커 사이를 교체
python3 tools/title-svg-generator.py            # (--inject 없이 돌리면 SVG만 출력)
```

std02 원본 대비 이 프로젝트에서 추가한 것:

- **자모 4개**: `ㅈ ㅋ ㅜ ㅔ`
- **E 규칙** — 복합모음(ㅘ ㅟ ㅢ 등) 조합. 초성과 가로모음이 왼쪽에 위아래로 쌓이고 세로모음이 오른쪽 전체 높이를 쓴다. `좌`(ㅈ+ㅗ+ㅏ), `퀴`(ㅋ+ㅜ+ㅣ)가 이걸 쓴다.
- **`충`은 D 규칙을 쓰지 않고 받침 박스를 따로 좁혔다** — D의 받침 박스(폭 84)에 `ㅇ`을 넣으면 심하게 납작해진다. std02에는 `ㅇ` 받침이 없어 드러나지 않던 문제다.

`SYL`에 없는 글자를 쓰면 `KeyError`가 난다 → 해당 음절의 조합을 `SYL`에 추가할 것. 새 자모가 필요하면 `JAMO`에도 추가.

이 스크립트는 **빌드 도구가 아니다** — 지워도 게임은 그대로 동작하고, 문구를 바꿀 때만 필요하다.

### `preview.html` — 개발 전용 뷰 검증 페이지

PRD §9 모듈2의 "더미 데이터로 각 `show*`를 호출해 4개 화면 확인" 지시를 페이지로 만든 것. `index.html`을 `fetch`해 body 마크업을 그대로 주입하므로 **마크업이 이중 관리되지 않는다**(`innerHTML`로 주입된 `<script>`는 실행되지 않아 `main.js`도 돌지 않는다). 하단 툴바로 시작/게임/정답팝업/오답팝업/완료/랭킹/빈랭킹/배경순환을 직접 띄워 본다.

## ✅ 모듈 3 — 게임 로직 & 사운드 (완료)

### `js/audio.js`

- **에셋 유무를 조사해 파일 재생과 합성음 중 하나를 고른다.**
  - BGM: 네 파일에 `HEAD` 요청 → **하나라도 있으면 파일 모드**, 전부 없으면 합성 모드. (일부만 넣으면 없는 트랙 차례에 소리가 끊기므로 넣으려면 4개 다 넣을 것)
  - 효과음: `correct`/`wrong`을 각각 독립 판별. 조사 전 첫 호출은 합성음으로 즉시 반응하고 결과는 다음 호출부터 반영한다(첫 정답에서 소리가 늦지 않도록).
- 합성 BGM은 **장5음계 아르페지오 + 베이스**를 Web Audio로 스케줄링한다. 트랙마다 조성·패턴·템포가 달라 `nextBgm()` 하면 분위기가 바뀐다. `LOOKAHEAD_MS`/`SCHEDULE_AHEAD`로 미리 예약해 끊김을 막는다.
- `startBgm()`은 **시작 버튼 클릭 시점**에만 불린다(자동재생 정책). `AudioContext`가 suspended면 `resume()`.
- 자동재생이 막히면 `play()` 실패를 조용히 삼킨다 — 소리 때문에 게임이 멈추면 안 된다.
- 계약 외 추가: `setMuted()`/`isMuted()` (main.js에서 **M 키**에 연결).
- 전역 `Audio` 생성자와 이름이 겹치므로 오디오 요소는 `document.createElement('audio')`로 만든다.

### `js/game.js` — 상태머신

`START → PLAYING → FEEDBACK → … → RESULT → RANKING`. `state.phase`로 전이를 막아 **중복 클릭·잘못된 시점의 입력을 무시**한다(`answer()`는 PLAYING에서만, `next()`는 FEEDBACK에서만, `retry()`/`finish()`는 RESULT에서만).

- 점수: 100에서 시작, 정답 +10 / 오답 −10.
- **배경·BGM은 분야가 바뀔 때만 전환한다.** PRD는 "문제(또는 분야) 전환마다"라고 했지만, 문제마다 `nextBgm()`을 부르면 파일 모드에서 곡이 매 문제 처음부터 다시 시작해 음악이 성립하지 않는다. 그래서 `lastCategory`가 바뀔 때만 전환한다 — 한 판에 BGM이 4번 바뀐다.
- `tally[category] = {correct, total}`로 분야별 정답을 누적하고, `buildSummary()`가 이걸로 정답률과 `byCategory`를 만든다.
- `retry()` — 문제를 새로 샘플링하고 100점 리셋, 이름은 유지.
- `finish()` — `saveRecord()` → `getRankings()` → `showRanking()`, `stopBgm()`. **저장이 실패해도(사생활 보호 모드 등) 이번 판 기록을 목록에 끼워 넣어 보여 준다** — 저장 실패로 성적을 못 보는 일이 없게.
- `highlightIndex`는 `playedAt + name`으로 찾는다(동점·동명이인 구분).

### `js/main.js` — 부팅 & 배선

- 보기 버튼은 **이벤트 위임**(`#choices`에 한 번만 바인딩) — `showQuestion`이 버튼을 매번 새로 그리기 때문에 개별 바인딩은 유지되지 않는다.
- 이름이 비면 시작 버튼 비활성화 + 안내 문구(F-1).
- 키보드: **1~4** 보기 선택, 팝업에서 **Enter/Space** 다음 문제, **M** 음소거. 이름 입력 중에는 단축키가 글자를 가로채지 않도록 `HTMLInputElement` 가드.
- `type="module"`은 defer지만 방어적으로 `readyState`를 확인해 부팅한다.

### 검증 결과

**정적 정합성 검사 통과** (JS가 참조하는 모든 `#id`/`.class`가 HTML에 존재, import 경로·export 이름 일치, PRD §9 계약 함수 14개 전부 존재, 버튼 5개 전부 바인딩됨). 로컬 서버로 전 리소스 200 응답 확인, `assets/*`만 404 → **폴백 경로가 실제로 타는 것을 확인**.

**아직 검증되지 않은 것 — 브라우저 수동 확인 필요.** 이 환경에는 브라우저도 JS 런타임도 없어서 실제 렌더·클릭·소리·`localStorage` 동작은 확인하지 못했다. 아래 체크리스트를 브라우저에서 직접 밟아야 한다.

## 통합 검증 체크리스트 (브라우저 수동)

`./run.sh`가 출력하는 주소로 접속(8000번이 막혀 있으면 8001번 등으로 자동 변경됨):

- [ ] 시작 화면에 🎯 과녁, 분야 카드 4개(🏯🔬🌍🎨), 이름 입력창이 보인다
- [ ] 제목이 **나무 널판지 + 못 4개**로 보이고, 글자가 금속 조각처럼 보이며 **리벳 구멍으로 나뭇결이 비친다**
- [ ] 브라우저를 확대(Ctrl +)해도 제목 글자가 벡터라 깨지지 않는다
- [ ] 이름이 비면 시작 버튼이 비활성, 입력하면 활성화된다
- [ ] 시작하면 100점에서 출발하고 상단바에 이름/진행(1/40)/점수가 보인다
- [ ] 분야가 바뀔 때 배경이 바뀌고(에셋 없으면 그라디언트) BGM 분위기가 바뀐다
- [ ] 보기를 고르면 즉시 팝업 + 효과음 + 해설이 뜨고, 정답 보기는 초록·고른 오답은 빨강으로 칠해진다
- [ ] 정답 +10 / 오답 −10이 점수에 반영되고, 진행률 막대가 늘어난다
- [ ] 40문제를 다 풀면 🏆 완료 화면에 최종점수·정답수·정답률·분야별 정답률이 정확히 나온다 (분야별 합계 = 정답 수)
- [ ] "다시 도전" 시 문제가 새로 샘플링되고 100점부터 다시 시작한다
- [ ] "종료" 시 랭킹에 이름·점수가 순위대로 추가되고, 내 기록이 강조된다
- [ ] **새로고침해도 랭킹이 유지된다** (`quiz.rankings`)
- [ ] 키보드 1~4 / Enter / M이 동작한다
- [ ] 브라우저 콘솔에 에러가 없다

`preview.html`에서 모듈2만 따로 확인할 수 있다.

## 사후 수정 이력 (실행해 보고 발견한 것들)

구현을 마친 뒤 사용자가 실제로 돌려 보며 나온 문제와 그 조치다. 같은 곳을 다시 건드릴 때 참고할 것.

### 1. `run.sh`가 파이썬 트레이스백을 뱉으며 죽음

**원인:** 8000번 포트를 **다른 python http.server가 이미 점유** 중이었다(이 리포지토리 환경에 상시 떠 있을 수 있다). `python3 -m http.server 8000`을 그대로 `exec` 하니 `OSError: [Errno 98] Address already in use`가 날것으로 노출됐다.

**조치:** 기본 실행은 8000~8020에서 빈 포트를 찾아 쓰고, 옮겼으면 그 사실을 알린다. 포트를 명시했는데 사용 중이면 **마음대로 옮기지 않고** 원인·해결책을 안내하고 종료한다. 겸사겸사 `--bind 127.0.0.1`로 외부 노출도 막았다.

### 2. `index.html`을 직접 열면 이름을 넣어도 시작 버튼이 안 켜짐

**원인:** 고장이 아니라 `file://` + ES Modules의 구조적 제약이다. `main.js`가 CORS로 차단돼 **이벤트가 하나도 바인딩되지 않으므로** 입력 핸들러도 안 붙어 버튼이 계속 비활성으로 남는다. **문제는 화면이 멀쩡해 보여 원인을 알 수 없다는 점**이었다.

**조치:** `index.html`에 일반 `<script>`로 `location.protocol === 'file:'`을 감지해 "로컬 서버로 열어 주세요" 안내를 띄운다. 근본 해결(=번들링해서 file://에서 돌리기)은 모듈 분리를 포기해야 하므로 택하지 않았다.

### 3. `run.sh`를 실행해도 브라우저가 안 뜸

**원인:** 원래 서버만 띄우는 스크립트였고 브라우저를 열지 않았다. 주소를 직접 입력해야 했다.

**조치:** `xdg-open`으로 자동 실행. 서버가 뜬 뒤 열리도록 1초 대기 후 백그라운드 실행하고, 그래픽 환경이 없으면 건너뛴다. `NO_OPEN=1`로 끌 수 있다.

### 4. 제목의 `충`에서 받침 `ㅇ`이 납작하게 뭉개짐

**원인:** D 규칙(초성+가로모음+받침)의 받침 박스 폭이 84라 `ㅇ`을 넣으면 가로로 늘어난다. std02에는 `ㅇ` 받침이 없어 드러나지 않던 문제였다.

**조치:** `충`만 D 규칙을 쓰지 않고 받침 박스를 폭 44로 좁혔다. PIL 렌더로 확인 후 수정.

## 유지보수 지점

- **문항만 늘리고 싶다** → `data/questions.js`만. 추가 후 `python3 tools/validate_bank.py`.
- **디자인만 바꾸고 싶다** → `css/style.css` + `js/views.js`만. `preview.html`로 확인.
- **제목 문구를 바꾸고 싶다** → `tools/title-svg-generator.py`의 `LINES`/`LABEL` 수정 후 `--inject`. `index.html`의 마커 사이를 손으로 고치지 말 것.
- **규칙·연출만 바꾸고 싶다** → `js/game.js` + `js/audio.js`만.
- **진짜 배경/BGM/효과음을 넣고 싶다** → `assets/README.md`의 파일명 그대로 넣기만 하면 된다. 코드 수정 불필요. BGM은 4개를 다 넣어야 한다(일부만 넣으면 없는 트랙 차례에 소리가 끊긴다).
- 분야를 추가하려면 `CATEGORIES` + `CATEGORY_ORDER` + `BG_FALLBACK`(views.js) 세 곳을 함께 고쳐야 한다. `validate_bank.py`의 `CATEGORY_ORDER`도 같이.

## 조작 요약 (사용자용)

- 보기 선택: 클릭 또는 **1~4** 키
- 팝업에서 다음 문제: 클릭 또는 **Enter / Space**
- 음소거 토글: **M** 키 (이름 입력 중에는 단축키가 동작하지 않는다)
