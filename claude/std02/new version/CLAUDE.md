# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this folder.

## Folder overview

`new version/`은 `../original version/`에서 완성된 "데일리 할 일 관리 앱"의 **다음 이터레이션**이다.

**현재 상태: 1차 리디자인 완료.** 기능(데이터 모델, CRUD, 필터, 진행률, 일정 상태 전환)은 원본과 100% 동일하고, **화면 디자인만 새로 입힌 상태**다.

파일 구성:

- `index.html` — 마크업 (원본 구조 + 제목/진행률 영역 교체). 제목 SVG가 인라인으로 들어 있다
- `style.css` — 새 디자인 (나무 널판지 제목, 금속 진행률)
- `app.js` — 원본 로직 이식 (저장 키와 진행률 표시만 변경)
- `tools/title-svg-generator.py` — **개발 전용** 제목 글자 SVG 생성기. 앱은 이 파일을 로드하지 않는다
- `화면캡쳐.png` — 리디자인 이전(원본) 화면 참조용 캡처
- `sample.jpeg` — 제목 글씨체의 디자인 레퍼런스("고맙습니다." — 리벳 구멍 뚫린 금속 조각 글씨)

## 원본 대비 달라진 점

### 1. 제목: "오늘의 할 일" → "김철민님의 오늘 하실 일들"

`<title>`과 화면 제목 모두 변경. 사용자 이름이 문구에 하드코딩되어 있다(다국어/다중 사용자 고려 없음).

### 2. 제목 디자인: 나무 널판지에 못을 박은 그래픽 (CSS만 사용)

이미지 파일 없이 CSS 그라디언트로만 그린다 — 외부 리소스 금지 제약을 지키기 위함.

- `.title-plank` — 널판지 본체
  - 나뭇결: `repeating-linear-gradient(178deg / 181deg …)` 두 겹(굵은 결 + 가는 결). 각도를 180deg에서 살짝 틀어 결이 완전 수평으로 떨어지지 않게 함
  - 옹이/얼룩: `radial-gradient(ellipse …)` 2개
  - 판재 바탕색: `linear-gradient(180deg, --wood-light → --wood-mid → --wood-dark)`
  - 입체감: `inset` 그림자 4겹(윗면 하이라이트 + 아래/좌우 음영) + 바깥 드롭섀도
  - `::before` / `::after` — 판재 위·아래 이음새 선
- `.nail` (`.nail-tl/tr/bl/br`) — 네 모서리에 박힌 못 머리
  - `radial-gradient(circle at 34% 28%, …)`로 금속 하이라이트, `::after`로 일자 홈(-18deg)
  - 바깥 `0 0 0 2px rgba(74,41,13,.35)`는 못이 나무를 파고든 자국

### 2-1. 제목 글자: 폰트가 아니라 SVG 그래픽 (`sample.jpeg` 참조)

`sample.jpeg`의 글씨체 — **획마다 리벳 구멍이 뚫린 둥근 금속 조각** — 를 재현한 것이다. 그런 폰트는 이 환경에 없고 외부 리소스 금지 제약상 가져올 수도 없으므로, **제목 글자를 폰트로 렌더링하지 않고 SVG 도형으로 조립해 그린다.**

- 마크업: `<h1 class="app-title">` 안에 인라인 `<svg class="title-art">`. `TITLE-ART:START/END` 주석 마커 사이는 **생성기 산출물이므로 직접 수정하지 말 것**
- 획 = `<polyline>` / `<ellipse>` + `stroke-linecap/linejoin: round` → 둥근 금속 조각 모양
- 리벳 구멍 = `<mask id="titleRivets">`의 **검은 원**. 흰 원을 덧그리는 게 아니라 실제로 뚫어야 뒤의 나뭇결이 비쳐 보인다
- 색은 `stroke="currentColor"` → CSS `.title-art { color: var(--plate-dark) }` 한 줄로 글씨 톤을 바꾼다
- 2줄 배치("김철민님의" / "오늘 하실 일들"). 1줄로 하면 음절당 폭이 좁아 받침이 뭉개져 읽히지 않는다
- `width: 100%; height: auto` — 벡터라 어떤 크기에서도 깨지지 않는다
- **접근성**: 화면에 텍스트가 없으므로 SVG의 `aria-label`과 `<title>`이 유일한 텍스트 대체물이다. 문구를 바꿀 때 이 둘도 반드시 함께 바뀌어야 한다(생성기가 자동 처리)

#### 제목 문구를 바꾸려면

```bash
cd "new version"
# tools/title-svg-generator.py 맨 아래 LINES/LABEL 수정 후
python3 tools/title-svg-generator.py --inject   # index.html의 마커 사이를 교체
```

- 생성기는 자모(`JAMO`)를 0~100 로컬 좌표계 도형으로 정의하고, 한글 조합 규칙 4가지(`A`~`D`: 초성+세로모음 / +받침 / 초성+가로모음 / +받침)로 음절을 조립한다
- `SYL`에 없는 글자를 쓰면 `KeyError`가 난다 → 해당 음절의 조합을 `SYL`에 추가할 것. 새 자모가 필요하면 `JAMO`에도 추가
- 좌표는 파이썬에서 절대좌표로 미리 변환하므로 어느 자모에서나 획 두께가 일정하다
- 이 스크립트는 **빌드 도구가 아니다** — 앱은 여전히 `index.html`을 브라우저에서 열기만 하면 동작하고, 파이썬은 문구를 바꿀 때만 필요하다. 필요 없어지면 이 파일만 지워도 앱은 그대로 돈다
- 튜닝 파라미터: `W`(획 두께) · `R_HOLE`(구멍 크기) · `INSET`(획 끝~구멍) · `MIN_GAP`(구멍 최소 간격, 이보다 가까우면 합쳐 획이 끊겨 보이지 않게 함) · `TILT`(음절별 미세 회전, 손으로 조립한 느낌)

### 3. 진행률: 금속(은빛) 스타일 + % 표시

- `.progress-plate` > `.progress-label` — "Progress Bar" 글씨
  - 글씨는 은빛 그라디언트를 `background-clip: text`로 입힘 (`color: transparent` + `-webkit-text-fill-color: transparent`)
  - **흰 카드 배경에서는 은색 글씨가 묻혀 보이지 않으므로** 어두운 금속 명판(`.progress-plate`)을 한 겹 감쌌다. `background-clip: text`를 쓰는 요소에는 판 배경을 같이 줄 수 없어 wrapper가 필요하다
- `.progress-bar` — 금속 홈(track). 어두운 금속 그라디언트 + 강한 `inset` 그림자로 파인 홈처럼 보이게 함
- `.progress-bar-fill` — 은빛 크롬 막대. 중앙에 어두운 띠를 넣어(50% 지점) 금속 반사선을 만든다
- `.progress-bar-shine` — 막대 위를 2.6초 주기로 훑고 지나가는 반사광 (`@keyframes metal-sweep`)
- `.progress-percent` — 완료/전체 건수 **옆에** 붙는 퍼센트 배지 (은빛 캡슐)
- 진행률 0%일 때는 `.is-empty` 클래스로 발광(box-shadow)을 끈다 — JS `updateProgress()`가 토글
- `@media (prefers-reduced-motion: reduce)`에서 반사광/경고 깜빡임 애니메이션 정지

### 4. 저장 키: `todos-app-v2`

데이터 모델은 원본과 동일하지만, 같은 브라우저에서 두 버전을 열었을 때 서로 간섭하지 않도록 키를 분리했다(원본은 `todos-app-v1` 소유). 따라서 **원본의 기존 데이터는 새 버전으로 넘어오지 않는다.**

### 5. JS 변경점 (그 외는 원본과 동일)

- `progressText` 하나였던 요소를 `progressCount`(완료/전체) + `progressPercent`(%) 두 개로 분리
- `updateProgress()`가 두 요소를 각각 갱신하고 `.is-empty`를 토글

## 유지되는 것 (원본 그대로)

- 데이터 모델: `{ id, title, category, completed, createdAt, startTime, durationMinutes }`
- `getScheduleStatus()` — 시계를 해석하는 **유일한** 함수. `'none' | 'upcoming' | 'warning' | 'overdue'`
- 1초 간격 `setInterval` 리렌더(편집 중일 때는 스킵해 포커스 유지)
- 시간 중복 시 `alert`로 차단, 기한 초과 항목은 완료 처리 불가(`enforceOverdueRules()`)
- 필터는 화면 표시만 바꾸고 `todos` 배열은 건드리지 않음, 진행률은 항상 전체 기준

## Inherited constraints (변경 금지)

- 순수 **HTML5 + CSS3 + vanilla JavaScript (ES6+)** — 프레임워크/외부 라이브러리/빌드 도구 없음
- **`index.html`을 브라우저에서 직접 열어** 실행 — 서버·백엔드·DB 없음
- 이미지/폰트 등 **외부 리소스 파일 금지** — 그래픽은 CSS 그라디언트와 인라인 SVG로 표현할 것
- 영속성은 **`localStorage`**만, 모든 접근은 try/catch + 빈 배열 폴백
- 자동화 테스트 없음. 검증은 브라우저 수동 확인, **F5 후 데이터 유지**가 핵심 시나리오

## 검증 체크리스트

- [ ] 제목이 널판지 + 못 4개로 보이는가
- [ ] 제목 글자가 금속 조각처럼 보이고, 리벳 구멍으로 나뭇결이 비치는가
- [ ] 브라우저 확대(Ctrl +)해도 글자가 벡터로 깨지지 않는가
- [ ] "Progress Bar" 명판과 은빛 막대가 보이고, 반사광이 흐르는가
- [ ] 완료 체크 시 막대 길이와 `완료 n / 전체 m` + `nn%`가 함께 갱신되는가
- [ ] 항목 0개일 때 0%, 막대 발광 없음
- [ ] F5 후에도 목록이 유지되는가 (`todos-app-v2` 키)
- [ ] 480px 이하에서 명판이 한 줄 전체를 쓰고 레이아웃이 깨지지 않는가
- [ ] 시간 중복 입력 차단 / 경고(깜빡임) / 기한 초과(취소선·체크 불가)가 원본과 동일하게 동작하는가

## Maintaining this file

기능이 추가되면 여기에 원본 대비 차이점, 데이터 모델·저장 키 변경, 단계별 작업 로그를 `../original version/CLAUDE.md`와 같은 형식으로 계속 기록할 것.
