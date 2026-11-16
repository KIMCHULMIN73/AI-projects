# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 — AI 공감 다이어리

오늘 있었던 일을 한 줄로 쓰면 AI가 감정을 분석하고(8감정 + 세기 1~5) 공감·위로 메시지를 돌려주는 일기 앱. 지난 일기는 브라우저 `localStorage`(`diary.v1.entries`)에 쌓인다. PRD는 아직 없다 — 아래 계약이 사실상의 사양이다.

```
./run.sh                 서버(127.0.0.1:8020 고정)를 띄우고 브라우저로 연다. 이미 떠 있으면 브라우저만. Ctrl+C로 종료
NO_OPEN=1 ./run.sh       서버만
python3 -B config.py     키 진단
```

`index.html`을 파일로 직접 열면(`file://`) 동작하지 않고 `./run.sh`로 실행하라는 안내만 나온다 — 키가 서버에만 있어야 해서 의도한 것이다.

### 구조

```
브라우저(index.html·style.css·app.js) ──POST /api/analyze──▶ server.py ──▶ llm.py ──▶ OpenRouter(무료 모델)
```

- `server.py` — `http.server`. 정적 파일은 허용 목록 세 개뿐(`/`→index.html), 나머지 404. Host가 `127.0.0.1:8020`·`localhost:8020`이 아니면 403. 입력 검증(공백 정리 후 1~200자, 본문 16KB, JSON만, 짝 없는 서로게이트 거부).
- `llm.py` — 모델 ID(이 파일 한 곳), 프롬프트, 응답 JSON 추출·검증, 재시도·폴백, 위기 안전망.
- `app.js` — 화면 상태(대기·성공·실패·비어 있음), 기록 저장. 서버·모델 문자열은 `textContent`로만. 오류 문구는 서버 `message`를 쓰지 않고 코드별로 클라이언트에서 정한다.

### API 계약

`POST /api/analyze` `{"text": "..."}` → 200 `{"emotion", "intensity", "message", "crisis"}`
- `emotion`: `기쁨`·`설렘`·`평온`·`슬픔`·`불안`·`분노`·`지침`·`외로움` 중 하나(목록 밖이면 동의어 표로 매핑, 없으면 `평온`). `intensity`: 1~5. `message`: 존댓말 2~4문장, 300자 이내.
- 실패 `{"error":{"code","message"}}`: `BAD_REQUEST`(400·413·415), `TOO_LONG`(400), `NO_KEY`(500), `RATE_LIMIT`(429), `DAILY_LIMIT`(429, `reset_at` = 한도가 풀리는 epoch ms를 함께 줄 수 있음), `TIMEOUT`(504), `UPSTREAM`(502), `BAD_OUTPUT`(502). 계약 밖으로 `FORBIDDEN_HOST`(403)·`NOT_FOUND`(404)·`METHOD_NOT_ALLOWED`(405)·`INTERNAL`(500).

### 모델 — 무료(`:free`)만 쓴다 (사용자 결정, 2026-11-13)

사용자는 DeepSeek V3.1 무료를 원했지만 OpenRouter에 DeepSeek 무료 모델이 없어 "다른 무료 모델"로 정했다. **이 계정은 크레딧이 있어 유료 모델을 부르면 실제로 과금된다** — `llm.py`는 `:free`가 아닌 ID면 멈춘다.

- 1순위 `baidu/ernie-5.5-flash-preview:free`(한국어가 가장 자연스러움, 응답 중앙값 약 3초), 폴백 `nvidia/nemotron-3.5-lightning:free`(다른 공급자). gemma-4는 공유 한도 429로, inkling은 403으로 제외. 비교는 `tools/compare_models.py`(`--list`는 호출 없이 목록만).
- ernie는 추론을 끌 수 없어(끄면 400) 추론 글만 빼고 `MAX_TOKENS` 2000(1200에선 200자 입력이 `length`로 잘림). nemotron은 추론을 켜면 content가 비므로 끈다. 모델별 옵션은 `MODEL_OPTIONS`.
- 재시도: 총 예산 40초, 한 번 25초, 최대 3회. 429가 하루 한도(`free-models-per-day`)면 폴백 없이 바로 `DAILY_LIMIT`(계정 전체 한도라 폴백도 같은 429), 그 밖의 429·5xx·타임아웃 → 폴백 모델, 해석 실패 → 같은 모델로 한 번 더, 그 밖의 4xx → 바로 `UPSTREAM`.
- **1순위가 preview 모델이라 사라지거나 유료로 바뀔 수 있다.** **무료 모델은 계정 전체로 하루 50회 한도다**(`free-models-per-day`, 응답 헤더 `X-RateLimit-Limit: 50`, 매일 09:00 KST 초기화. OpenRouter에 크레딧을 10달러 이상 충전한 계정은 1000회). 다 쓰면 두 모델 모두 즉시 429를 준다 — 개발·시험 중 실제 호출도 이 한도에서 빠진다(2026-11-13 하루에 소진).

### 위기(crisis) 처리 — 가장 조심할 곳

- crisis = 모델 판정 **또는** 서버 키워드 안전망. true면 화면에 자살예방상담전화 109, 정신건강위기상담 1577-0199가 뜬다.
- **키워드에 걸린 입력은 모델이 어떤 이유로 실패해도(키 없음 포함) 200 + 고정 위로문(`CRISIS_FALLBACK`)을 돌려준다.** 오류만 보내면 상담 번호가 안 나오기 때문이다.
- 키워드 비교는 글자·숫자만 남긴 원문으로. "끝내고 싶다"는 대상이 삶·전부일 때만, 관용 구간(유서 깊은, 자살골, 너 죽을래, 긍정어+"죽고 싶을 정도")은 지운 뒤 찾는다. "~해 죽겠다"는 모델에 맡긴다. 애매하면 놓치지 않는 쪽("부끄러워 죽고 싶었다"는 위기로 잡힌다). 측정: 명확한 위기 23/23 검출, 관용 13개 오탐 0 — `tools/check_api.py`에 고정되어 있으니 키워드를 고치면 반드시 돌릴 것.

### 검증 (모두 `python3 -B`)

```
tools/check_api.py                       API 계약·오류 매핑·위기 폴백·키워드 표 (가짜 업스트림, 모델 0회)
tools/check_frontend.py --out DIR        화면 61항목 (tools/mock_server.py, 모델 0회)
tools/check_e2e.py --out DIR             실제 server.py + 실제 화면 + 가짜 업스트림 33항목 (모델 0회)
tools/check_e2e.py --live --out DIR      run.sh 실제 실행, 실제 모델 호출, 키 누출·Ctrl+C 종료 확인 (8020이 비어 있어야 함)
tools/check_live_inputs.py [--only 1,5]  실제 입력 19종 (모델 호출, 횟수 집계)
```

스크린샷 `DIR`은 저장소 밖에 둔다. 실제 모델을 부르는 것은 `--live`와 `check_live_inputs.py`뿐이다.

### 작업 로그

- **2026-11-13** — 서브에이전트로 구현. backend-developer(server·llm·run.sh)와 frontend-developer(화면 3파일, 가짜 서버로 확인)가 위 계약을 공유해 동시에 만들고, quality-assurance-engineer가 처음으로 합쳐 시험하며 8건을 고쳤다(이번에 한해 앱 코드 수정 권한을 줌): 위기 입력+모델 실패 시 상담 번호 미표시(→ 고정 응답), "살아야 할 이유를 모르겠다" 같은 간접 표현 미탐, 키워드 미탐 6·오탐 7, 클라이언트 끊김 시 트레이스백, 서로게이트 입력이 모델까지 감, 200자 입력 `length` 재시도(10.7초→3.8초), run.sh 종료 신호 처리, 대기 안내 문구를 실측에 맞춤. 실제 모델 호출은 백엔드 36회 + QA 28회, 모두 무료.
- **2026-11-16** — 하루 한도를 다 쓰면 "1분쯤 뒤에"라고 잘못 안내하던 것을 고쳤다: `DAILY_LIMIT` 코드를 새로 두고 화면에 "내일 오전 9시부터 다시 답장해 드릴게요"처럼 풀리는 시각을 보여준다(위기 입력은 여전히 고정 위기 응답). 배경 빛 번짐이 body 높이마다 반복돼 짧은 페이지에서 띠가 생기던 것을 `min-height: 100vh` + `background-attachment: fixed`로 고쳤다 — 이 때문에 전체 페이지 캡처(`captureBeyondViewport`)에서는 첫 화면 아래가 민무늬로 찍히지만 실제 화면에서는 스크롤해도 이어진다(뷰포트 캡처로 확인).
- 남은 확인: 실제 한글 IME로 조합 중 엔터, Firefox.

### 서브에이전트 (`.claude/agents/`) — 모두 커밋은 하지 않는다

| 이름 | 맡는 일 | 고치는 범위 |
|---|---|---|
| `product-manager` | PRD(목표·요구사항·단계)와 일정표 | 문서만. 형제 프로젝트의 PRD 구조를 따르지 않고 새로 정한다(진입점은 `PRD.md`, 일정표도 거기에만) |
| `backend-developer` | 서버 구조, API 계약, 데이터 저장, 외부 호출, 키 처리 | 서버 코드 |
| `frontend-developer` | 화면, 반응형(넓은 화면·480px), 접근성 | 클라이언트 코드 |
| `ai-integration-expert` | OpenRouter·DeepSeek 호출, 프롬프트, 응답 검증 | 모델 호출 모듈·프롬프트 |
| `quality-assurance-engineer` | 기능·오류·성능 검증, 코드 리뷰, 사용성 제안 | `tools/` 검증 스크립트만. 앱 코드는 보고만 |

사양은 `product-manager`만 고친다. 다른 에이전트는 사양과 어긋난 점을 보고만 한다.

## OpenRouter API 키 — 형제 프로젝트와 같은 키를 쓴다

키는 저장소 **밖** `~/.config/openrouter/env`(디렉터리 700, 파일 600) 한 곳에 있고 std04와 함께 쓴다. 저장소 안에는 키가 없다.

- **키를 읽는 통로는 `config.py` 하나뿐이다.** 찾는 순서: 환경변수 `OPENROUTER_API_KEY` → `~/.config/openrouter/env` → `std05/.env`(폴백, 경고). 다른 코드에서 `.env`를 직접 열지 않는다. `config.py`는 std04의 것을 그대로 복사했다(경로 주석만 다름).
- 호출 헤더는 `config.auth_header()`로 만들고, 예외·응답·헤더를 찍을 때는 `config.redact()`를 거친다.
- 키는 서버에서만 쓴다. 브라우저로 내려가는 경로를 만들지 않는다.
- 설정·교체 방법은 `.env.example`에 있다. `.gitignore`가 `.env`·`.env.*`·`*.key`·`*.pem`·`secrets/`를 막는다.

```
python3 -B config.py    # 키 진단: 출처·길이·형식·마스킹만 찍는다(키 자체는 안 찍음). 정상이면 종료코드 0
```

2026-11-13 확인: 키 출처는 `~/.config/openrouter/env`, 형식 정상, `GET /api/v1/key` 200(무료 등급).

## 리포지토리 관례 (형제 프로젝트에서 이어지는 것)

git 루트는 두 단계 위(`../..`, `AI-projects`)이고 원격은 공개 GitHub이다. 형제(`../std01` 숫자 인식기, `../std02` 할 일 관리 앱, `../std03` 퀴즈 게임, `../std04` 냉장고 사진 레시피 추천)와 공유하는 코드·의존성은 없지만 작업 방식은 이어진다:

- **프레임워크·빌드 도구·번들러·npm 없음.** vanilla로 짜고 바로 실행한다. 실행기가 필요하면 `run.sh`를 둔다(std03·std04).
- **외부 의존성 없음.** Python은 표준 라이브러리만 쓴다(`requests`·`python-dotenv`도 쓰지 않는다).
- **자동화 테스트 러너 없음.** 검증은 수동이거나 `tools/` 아래 검증 스크립트로 한다.
- **개발 전용 스크립트는 `tools/`에 두고 앱이 로드하지 않게 한다** — 지워도 앱이 돌아야 한다.
- **사양이 있으면 PRD가 원본이다.** 사양과 어긋나게 구현해야 하면 PRD와 이 문서를 함께 고친다.
- **이 문서를 작업 로그로 계속 갱신한다.** 설계 판단, 사양과 어긋난 지점, 그 이유를 남긴다.
- 반복 작업은 `.claude/commands/`에 슬래시 명령어로, 검토·최적화 같은 역할은 `.claude/agents/`에 서브에이전트로 정리한다(std03·std04).
- 문서는 한국어로 쓴다.

## 이 환경에서 쓸 수 있는 것 (Raspberry Pi, Linux)

- **Python 3.13.5**, **curl**, **chromium·firefox** — 있다.
- **node / npm / jq — 없다.** JS 문법 검사(`node --check`)는 못 쓰고, JSON은 `python3 -m json.tool`로 다룬다.
- 브라우저 자동화 라이브러리(playwright·selenium)는 없다. 화면 확인을 자동화해야 하면 std04의 `tools/check_browser_step*.py`처럼 표준 라이브러리로 CDP를 직접 다룬다.
