# UNIT — 로컬 프록시

**쓰는 단계: 1 · 2 · 3** · 구현 파일: `server.py`

브라우저 대신 OpenRouter를 부르는 로컬 서버. **이 프로젝트에 서버가 있는 유일한 이유는 키를 브라우저에서 숨기기 위해서다.** 그 외의 일(계산·저장)을 여기로 끌어오지 말 것 — 서버가 커질수록 "정적 파일만 열면 돌아간다"는 형제 프로젝트의 성질에서 멀어진다.

## 책임

| 한다 | 하지 않는다 |
|---|---|
| 키를 붙여 OpenRouter에 중계 | 레시피·프로필 저장 (localStorage가 한다) |
| `Models`에서 모델 ID 읽기 | 재료 목록 가공·점수 계산 (브라우저가 한다) |
| 프롬프트 보관 | 사용자 인증 |
| 정적 파일 서빙 | 외부 네트워크 노출 |

## 실행

```bash
python3 server.py            # 빈 포트를 찾아 띄우고 브라우저를 연다
python3 server.py 8080       # 포트 고정
NO_OPEN=1 python3 server.py  # 브라우저 자동 실행 없이
```

- **`127.0.0.1`에만 바인딩한다.** `0.0.0.0`으로 열면 같은 망의 누구나 내 키로 API를 쓸 수 있다.
- 8000번은 이 환경에서 다른 http.server가 쓰고 있을 수 있다. **빈 포트를 자동으로 찾는다**(std03이 같은 이유로 그렇게 한다).
- 그래픽 환경(`DISPLAY`/`WAYLAND_DISPLAY`)이 있을 때만 `xdg-open`으로 브라우저를 연다.

## 라우팅

| 경로 | 메서드 | 용도 | 단계 |
|---|---|---|---|
| `/` 및 정적 파일 | GET | `index.html`, `app.js`, `style.css` | 1·2·3 |
| `/api/health` | GET | 키를 읽을 수 있는지, 모델이 무엇인지 | 1 |
| `/api/vision` | POST | 이미지 → 식재료 | 1 |
| `/api/recipe` | POST | 식재료 → 레시피 | 2 |

`/api/health` 응답에 **키를 넣지 않는다.** 담는 것은 `{"ok": true, "models": {"vision": "...", "text": "..."}}` 뿐이다. 화면 상단에 "준비됨"을 띄우고, 키가 없을 때 사용자에게 `.env.example`을 안내하기 위한 것이다.

## 응답 규약

성공:
```json
{ "ok": true, "data": { ... }, "meta": { "elapsed_ms": 18900, "tokens_in": 569, "tokens_out": 2182 } }
```

실패:
```json
{ "ok": false, "error": { "code": "RATE_LIMIT", "message": "사람이 읽을 한국어 한 줄", "retryable": true } }
```

- **OpenRouter의 원본 응답을 그대로 흘리지 않는다.** 필요한 필드만 골라 담는다. 오류 본문에는 요청 헤더가 섞여 나오는 경우가 있어 그대로 넘기면 키가 브라우저 콘솔에 남을 수 있다.
- 프록시에서 나가는 모든 문자열은 `config.redact()`를 통과시킨다.
- `meta`는 화면에 쓰지 않아도 반드시 담는다 — 1단계가 20초씩 걸리는 이유를 나중에 추적할 수 있는 유일한 단서다.

### 오류 코드

| code | 언제 | retryable | 화면 문구(예) |
|---|---|---|---|
| `NO_KEY` | `config.MissingKeyError` | false | 키가 설정되지 않았습니다. `.env.example`을 보세요. |
| `RATE_LIMIT` | OpenRouter 429 | true | 무료 모델 사용량이 찼습니다. 잠시 후 다시 시도하세요. |
| `UPSTREAM` | OpenRouter 5xx | true | 모델 서버가 응답하지 않습니다. |
| `TIMEOUT` | 응답 없음 | true | 시간이 너무 오래 걸립니다. |
| `BAD_OUTPUT` | JSON 파싱 실패 (→ `UNIT_json_contract.md`) | true | 결과를 읽지 못했습니다. 다시 시도하세요. |
| `BAD_REQUEST` | 입력 검증 실패 | false | (입력 문제를 구체적으로) |

## 입력 검증

프록시는 브라우저를 믿지 않는다. **브라우저가 보낸 값이 그대로 OpenRouter 요청이 되게 두지 말 것.**

- 모델 ID는 요청 본문에서 받지 않는다. **항상 `Models` 파일에서 읽는다.** (요청으로 모델을 고르게 하면 유료 모델을 호출당할 수 있다.)
- `max_tokens`·`temperature` 등 파라미터도 요청에서 받지 않는다. 단계별 고정값을 서버가 정한다.
- 이미지 본문 크기 상한을 둔다(→ `UNIT_image_input.md`). 넘으면 `BAD_REQUEST`.

## 검증

- [ ] `curl -s localhost:PORT/api/health` 가 모델 두 개를 돌려주고 키는 없다
- [ ] 키를 숨긴 채(`OPENROUTER_API_KEY= ` 환경변수로 빈 값 주입) 띄우면 `NO_KEY`가 뜬다
- [ ] 응답 어디에도 `sk-or-v1-`이 없다 — `curl ... | grep -c 'sk-or-v1-'` 가 0
- [ ] `0.0.0.0`이 아니라 `127.0.0.1`에 바인딩되어 있다 (`ss -ltnp | grep PORT`)
