# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 현재 상태 — 앱 코드는 아직 없다

지금 있는 것은 **OpenRouter 키를 안전하게 다루기 위한 바탕**뿐이고, 앱 자체는 한 줄도 없다.

- `config.py` — 키를 찾아 주는 로더. **키를 파일에서 읽는 코드는 이것 하나뿐이어야 한다.**
- `.env.example` — 설정 방법을 적어 둔 이름표. 커밋되는 유일한 env 파일이다(실제 값 없음).
- `tools/check_secrets.py` — 키가 새어 나갈 곳을 훑는 점검기. 개발 전용이다.
- `tools/smoke_test_api.py` — 두 모델이 실제로 응답하는지 확인하는 연기 테스트. 개발 전용이다.
- `.gitignore` — 비밀 파일 패턴.
- `Models` — 쓸 모델 메모. `.gitignore`에 걸려 있어 커밋되지 않는다(사용자 결정).

**첫 코드를 넣는 사람이 이 절을 지우고 개요·실행법·아키텍처로 바꿔 써야 한다.**

## 쓰는 모델 — `Models` 파일이 원본이다

```
[Image API model]
dots-studio/dots-3-note-preview:free

[Text API model]
inclusionai/ling-3.0-flash-fin:free
```

둘 다 OpenRouter의 **무료(`:free`) 모델**이고, 2026-09-04 기준 실제 호출로 동작을 확인했다.

| 용도 | 모델 | 입력 | 출력 | 컨텍스트 |
|---|---|---|---|---|
| 이미지 인식 | `dots-studio/dots-3-note-preview:free` | 텍스트+이미지 | 텍스트 | 512,000 |
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

마지막 실행 결과 (2/2 통과):

```
텍스트  inclusionai/ling-3.0-flash-fin:free   0.9초  63→76토큰   "1) 서울  2) 391"
이미지  dots-studio/dots-3-note-preview:free  6.0초  260→588토큰 "1) STD04-7391
                                                      2) 빨간색 원, 파란색 정사각형, 초록색 삼각형"
```

### 코드를 쓸 때 걸리는 지점

- **이미지 모델은 추론형이다.** 최종 답은 `message.content`, 생각은 `message.reasoning`에 나뉘어 온다. `max_tokens`가 모자라면 **`content`가 빈 문자열인 채로 끝난다** — 실제로 300토큰에서 그랬다. 응답이 비었다고 실패로 처리하지 말고 `finish_reason`을 함께 볼 것. 이미지 한 장에 프롬프트만 260토큰이 든다.
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
