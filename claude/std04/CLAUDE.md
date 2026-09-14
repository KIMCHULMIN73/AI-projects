# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 — 냉장고 사진으로 레시피 추천

냉장고 사진을 올리면 식재료를 뽑아내고, 그 재료로 만들 레시피를 추천하고, 프로필별로 저장하는 웹 앱이다.

**사양은 [PRD.md](PRD.md)가 원본이다.** 규칙·데이터 계약·단계 경계를 바꿔야 하면 PRD와 이 문서를 함께 고칠 것.

```
PRD.md              전체 색인 · 확정된 결정과 근거 · 실측 수치
PRD_step1.md        이미지 → 식재료          (구현됨 — 비전 모델 문제로 완료 기준 미달, 아래 작업 로그)
PRD_step2.md        식재료 → 레시피          (아직 구현 없음)
PRD_step3.md        프로필 + 레시피 보관함   (아직 구현 없음)
units/              여러 단계가 공유하는 사양 6개
config.py           키를 읽는 유일한 통로
tools/              개발 전용 (앱이 로드하지 않는다)
.claude/commands/   /prd-step · /unit · /prd-check · /api-measure
```

**현재 상태: 1단계 코드(`server.py`·`index.html`·`style.css`·`storage.js`·`app.js`)가 있다.** 실행은 `python3 server.py`. 다만 **실제 냉장고 사진에서 지금의 비전 모델이 60초 안에 답을 끝내지 못해** 완료 기준을 채우지 못했다 — 맨 아래 "작업 로그"를 먼저 볼 것.

### 구조 — 왜 서버가 있는가

```
브라우저 ──fetch──▶ 로컬 프록시(server.py) ──▶ OpenRouter
```

웹 앱인데 **브라우저가 OpenRouter를 직접 부를 수 없기 때문이다.** 부르는 순간 키가 노출된다(아래 "절대 하지 말 것"). 프록시는 그 제약 때문에 생겼고, 덕분에 브라우저는 모델 이름조차 모른다 — 모델 교체는 `Models` 파일만 고치면 끝난다.

## 쓰는 모델 — `Models` 파일이 원본이다

```
[Image API model]
inclusionai/ling-3.0-flash-vl:free

[Text API model]
inclusionai/ling-3.0-flash-fin:free
```

둘 다 OpenRouter의 **무료(`:free`) 모델**이다. 이미지 모델은 **2026-09-14에 `dots-studio/dots-3-note-preview:free`에서 교체했다** — 실제 냉장고 사진에서 60초 안에 답을 끝내지 못했기 때문이다(맨 아래 작업 로그).

| 용도 | 모델 | 입력 | 출력 | 컨텍스트 |
|---|---|---|---|---|
| 이미지 인식 | `inclusionai/ling-3.0-flash-vl:free` | 텍스트+이미지 | 텍스트 | 262,144 |
| 텍스트 | `inclusionai/ling-3.0-flash-fin:free` | 텍스트 | 텍스트 | 262,144 |

- 이미지 모델은 **그림을 만들지 않는다.** 입력으로 이미지를 받고 출력은 텍스트뿐인 **인식(vision)** 모델이다. 이미지 생성이 필요하면 다른 모델을 골라야 한다.
- `Models`는 git에서 제외했으므로 **클론해도 따라오지 않는다.** 위 표가 사실상의 사본이니 모델을 바꾸면 두 곳을 함께 고칠 것.
- `Models`의 형식은 이미 한 번 바뀌었다(`image model : ...` → `[Image API model]` + 다음 줄). `tools/smoke_test_api.py`의 `read_models_file()`이 두 형식을 모두 받고, 슬래시가 있는 줄을 모델 ID로 보므로 형식이 또 바뀌어도 대개 견딘다.

## API 동작 확인

```bash
python3 tools/smoke_test_api.py                    # Models에 적힌 두 모델 모두
python3 tools/smoke_test_api.py --only image
python3 tools/smoke_test_api.py --text-model z-ai/glm-5.2:free   # 다른 모델을 시험할 때
```

**답을 미리 아는 질문만 던진다.** 모델이 그럴듯하게 답하는 것과 API가 실제로 도는 것은 다르기 때문이다. 이미지 테스트는 스크립트가 그린 그림(도형 3개 + 코드 `STD04-7391`)을 보내 **코드값을 읽어 오는지**로 판정한다 — 그림을 실제로 본 경우에만 통과한다. 이미지 생성에만 Pillow를 쓰며 **앱의 의존성이 아니다.**

이미지 모델 교체 전(2026-09-04)의 실행 결과 (2/2 통과):

```
텍스트  inclusionai/ling-3.0-flash-fin:free   0.9초  63→76토큰   "1) 서울  2) 391"
이미지  dots-studio/dots-3-note-preview:free  6.0초  260→588토큰 "1) STD04-7391
                                                      2) 빨간색 원, 파란색 정사각형, 초록색 삼각형"
```

### 코드를 쓸 때 걸리는 지점

- **이미지 모델은 추론형이다**(옛 dots·지금 ling-vl 모두). 최종 답은 `message.content`, 생각은 `message.reasoning`에 나뉘어 온다. `max_tokens`가 모자라면 **`content`가 빈 문자열인 채로 끝난다** — dots는 300토큰에서, 실제 냉장고 사진에서는 3000토큰에서도 그랬다. 응답이 비었다고 실패로 처리하지 말고 `finish_reason`을 함께 볼 것. 이미지 한 장에 프롬프트만 260토큰이 든다.
- 호출 형식은 OpenAI 호환이다. 이미지는 `content` 배열에 `{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}`로 넣는다.
- `urllib.request`만으로 충분하다 — `requests`를 끌어들이지 말 것.

## 키 취급 — 반드시 지킬 것

실제 키는 **저장소 밖**에 있다.

```
~/.config/openrouter/env      # 디렉터리 700, 파일 600
```

`std04/.env`는 **없다.** 예전에 여기 있었지만 위 경로로 옮겼다. 저장소 안에 두면 위험한 이유는 두 가지다:

1. `.gitignore` 한 줄에만 목숨을 건다. `git add -f`, 패턴 오타, 다른 디렉터리로의 복사 한 번이면 공개 원격(`github.com/KIMCHULMIN73/AI-projects`)으로 넘어간다.
2. **형제 프로젝트들은 프로젝트 폴더를 그대로 정적 서버로 띄운다**(`std03/run.sh`의 `python3 -m http.server`). 그 폴더에 `.env`가 있으면 `http://127.0.0.1:PORT/.env`로 그냥 받아진다.

### 코드에서 키를 얻는 법

`.env`를 직접 파싱하지 말고 항상 `config.py`를 거칠 것. 탐색 순서·권한 점검·마스킹이 한 곳에 모여 있다.

```python
import config

key = config.get_api_key()          # 환경변수 → ~/.config/openrouter/env → (구식) ./.env
headers = config.auth_header()      # Authorization 헤더까지 만들어 준다
print(config.redact(err))           # 로그·예외를 찍기 전에 반드시 통과시킬 것
```

- 한 번만 쓰고 말 때는 환경변수가 가장 안전하다: `OPENROUTER_API_KEY=... python3 x.py` — 디스크에 남지 않는다.
- `config.py`는 저장소 안의 `.env`를 발견하면 **동작은 하되 경고**한다. 경고가 보이면 옮길 것.

### 절대 하지 말 것

- **키를 브라우저로 내려보내지 말 것.** JS·HTML·`fetch`에 키가 들어가면 그 순간 사용자에게 전부 노출된다 — 난독화·빌드 시 주입 전부 소용없다. 형제 프로젝트가 전부 브라우저 앱이라 여기서 실수하기 쉽다. **화면이 필요하면 키를 쥔 로컬 파이썬 프록시를 두고 브라우저는 그 프록시만 부르게 할 것**(구조를 정할 때 사용자와 합의할 것).
- 키를 소스·설정·주석·커밋 메시지·`Models`·이 문서에 적지 말 것.
- API 응답이나 예외를 그대로 찍지 말 것. 요청 헤더가 함께 덤프되면 키가 로그에 박힌다. `config.redact()`를 쓸 것.
- 키가 든 파일을 프로젝트 폴더로 복사해 오지 말 것.

### 점검

커밋 전, 그리고 파일을 새로 만든 뒤에 돌린다.

```bash
python3 tools/check_secrets.py            # 로컬 점검만 (네트워크 안 씀)
python3 tools/check_secrets.py --online   # + OpenRouter에 키가 살아있는지 확인
python3 config.py                         # 키를 어디서 읽는지 진단 (키 자체는 안 찍는다)
```

점검 항목: 추적 파일·git 히스토리·작업트리에 키 문자열이 없는가, `.gitignore`가 하위 디렉터리까지 덮는가, `.env.example`에 진짜 값이 없는가, 키 파일 권한이 600/700인가.

`git check-ignore`의 **종료코드로 판정하지 말 것** — 부정 규칙(`!.env.example`)에 걸려도 0을 준다. 어떤 패턴에 걸렸는지를 봐야 한다(`check_secrets.py`의 `is_ignored()`가 그렇게 한다).

### 키가 유출됐다면

지우고 다시 커밋해도 소용없다 — 히스토리와 GitHub 캐시에 남는다. **openrouter.ai/keys에서 해당 키를 폐기(revoke)하고 새로 발급하는 것이 유일한 조치다.** 그다음 `~/.config/openrouter/env`를 갱신한다.

## 이 환경에서 쓸 수 있는 것

- **Python 3.13.5** — 있다. 표준 라이브러리만 쓴다(`urllib.request`로 API 호출 가능). 외부 의존성을 두지 않는 것이 리포지토리 관례라 `requests`·`python-dotenv`도 쓰지 않는다.
- **curl** — 있다.
- **node / npm — 없다.** 형제 프로젝트에서 쓰던 `node --check` 같은 문법 검사는 여기서 못 쓴다.
- **jq — 없다.** JSON은 `python3 -m json.tool`이나 짧은 python으로 다룰 것.

## 리포지토리 관례 (형제 프로젝트에서 이어지는 것)

git 루트는 두 단계 위(`../..`, `AI-projects`)이고 원격은 공개 GitHub이다. 형제(`../std01` 숫자 인식기, `../std02` 할 일 관리 앱, `../std03` 퀴즈 게임)와 공유하는 코드·의존성은 없지만 작업 방식은 이어진다:

- **프레임워크·빌드 도구·번들러·npm 없음.** vanilla로 짜고 바로 실행한다.
- **자동화 테스트 러너 없음.** 검증은 수동이거나 `tools/` 아래 검증 스크립트로 한다(`check_secrets.py`가 그 첫 예다).
- **개발 전용 스크립트는 앱과 분리한다.** `tools/`에 두고 앱이 로드하지 않게 해서, 지워도 앱이 도는 상태를 유지한다.
- **`CLAUDE.md`를 작업 로그로 계속 갱신한다.** 설계 판단, 사양과 어긋난 지점, 그 이유를 남긴다.
- 반복 작업이 생기면 `.claude/commands/`에 슬래시 명령어로 정리한다(`std03`의 방식).
- 문서는 한국어로 쓴다.

## 작업 로그

### 2026-09-14 — 1단계 구현 (`/prd-step 1`)

만든 파일: `server.py`(프록시) · `index.html` · `style.css` · `storage.js` · `app.js`. 테스트 사진은 `sample_fridge.jpg`(1077×716).

**사양을 바꾼 곳 — 전부 사용자 결정, 문서를 먼저 고쳤다**

| 무엇 | 왜 |
|---|---|
| 이름 판정에 "공백 없이 한글·영문이 붙음" 추가, 음절 반복은 **3연속**만 (`UNIT_json_contract`) | 원래 규칙으로는 사양의 대표 예시 `달modification달걀`을 못 잡는다. 떨어진 반복은 `토마토`·`바나나`에도 있다 |
| 타임아웃은 **요청 하나의 총 예산**, 타임아웃은 재시도 안 함 (`UNIT_model_call`·`UNIT_ui_state`) | 시도마다 60초 + 재시도면 최대 2분 → "60초에 TIMEOUT"과 충돌 |
| `NO_MODEL` 오류 코드 추가, `NO_KEY`에 401·403, `UPSTREAM`에 그 밖의 4xx·내부 오류 (`UNIT_proxy`) | 코드표에 해당 칸이 없었다 |
| **이미지 모델 교체** dots-3-note-preview → ling-3.0-flash-vl | 아래 실측 |
| 1단계 JSON: 스키마 강제 → **프롬프트 형태 예시 + 복구** (`UNIT_json_contract`) | ling-vl은 `response_format` 미지원. 4회 실측 모두 펜스 없이 파싱 성공 |
| `max_tokens` 3000 → **6000** (`UNIT_model_call`) | 실제 사진 출력 3,110~4,615 토큰 |
| 사진 긴 변 1024 → **768px** (`UNIT_image_input`) | 22초(1024급은 33초). 대가: 수량 인식이 회차마다 들쭉날쭉하다 |
| 프롬프트: 이름에 괄호·영어·슬래시 금지, 한 항목에 재료 하나, 수량에 위치 금지 (`PRD_step1`) | dots가 `우유 (Milk)`, 수량 칸에 선반 위치, `간장, 고추장…` 묶음을 냈다 |

**실측 — 왜 모델을 바꿨나** (`sample_fridge.jpg` 기준, 첫 호출은 옛 사진 `냉장고식재료`)

| 모델 · 사진 | 결과 |
|---|---|
| dots · 원본, max_tokens 3000 | 38초 `finish=length` → `BAD_OUTPUT` |
| dots · 원본, 8000 / effort=low / reasoning 상한 2000 | 94초·66초·88초, 셋 다 8000토큰 소진, 답 없음 — **추론 제한 옵션을 무시한다** |
| dots · 512px | 49초, 재료 8개, `달걀`→`달란` (전부 한글이라 이름 규칙에 안 걸린다) |
| gemma-4-31b / 26b-a4b | 429 (무료 한도, 두 번) |
| gemma-3-27b-it:free | 404 — 무료 판이 없어졌다(유료만) |
| nex-n2.5-pro / mini | 240초 초과 / 400 |
| inkling / inkling-small | 403 — agentic harness 전용 |
| **ling-3.0-flash-vl** · 512 ×2 / 768 / 원본 | **24·24·22·33초**, 전부 `stop`·파싱 성공·enum 이탈 0, 재료 10~12개 |

**사양에 없어서 정한 것**

- 정적 파일은 **이름 화이트리스트 5개만** 연다. 폴더를 통째로 서빙하면 `Models`·`config.py`가 나간다.
- **Host 헤더 검사**(DNS 리바인딩)와 **POST는 `application/json`만**(다른 사이트의 form·text/plain "단순 요청"은 CORS 사전 확인 없이 도착해 내 무료 한도를 쓴다).
- `read_models_file()`을 `server.py`에 **복제**했다. `tools/`를 import하면 tools를 지웠을 때 앱이 죽는다. `Models` 형식을 바꾸면 두 곳을 고칠 것.
- 확장자 없는 파일은 브라우저가 `type`을 비워 보낸다 → 타입으로 거절하지 않고 열어 보고 판단한다.
- 모델이 맞게 읽었는데 표시가 붙은 항목은 **배지를 눌러 확인**할 수 있다(고칠 값이 없으면 표시를 풀 방법이 없었다).
- 손으로만 만든 목록은 `analyzed_at: null`.
- 경과 초는 `aria-live` 밖에 둔다. 안에 두면 낭독기가 매초 읽는다.

**교체 후 검증 결과**

- 프록시 경유 실측: 실제 사진 768px **22.4초·재료 13개·복구 없음**, 식재료 없는 그림 2초·`items` 0개. 두 응답 모두 `sk-or-v1-` 0건.
- `tools/smoke_test_api.py` 2/2, `tools/check_secrets.py` 전부 통과. 라우팅·입력 검증·`127.0.0.1` 바인딩·`NO_KEY`·`NO_MODEL` curl 점검 통과.
- **브라우저 쪽 체크리스트(카테고리별 화면, 경과 초, 편집·F5 유지, 480px, 프록시를 끈 뒤의 오류, 붙여넣기, PDF 거부, EXIF 회전)는 아직 `/prd-check 1`로 돌리지 않았다.**

**검증하다 알게 된 것**

- 사양의 NO_KEY 검증법 `OPENROUTER_API_KEY= `(빈 값)은 **재현되지 않는다** — `config.py`가 빈 값을 건너뛰고 `~/.config/openrouter/env`를 읽는다. `HOME=<빈 폴더> python3 server.py`로 재현할 것.
- 서버 출력을 파일로 돌리면 블록 버퍼라 주소가 안 찍힌다 → `line_buffering`으로 고쳤다.
- 이 기기에는 8000~8007번에 오래된 `http.server`들이 떠 있고, **8000번은 `0.0.0.0`에 열려 있다**(이 프로젝트와 무관, 건드리지 않았다).
