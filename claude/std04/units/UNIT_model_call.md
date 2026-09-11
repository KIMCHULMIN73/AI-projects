# UNIT — OpenRouter 호출

**쓰는 단계: 1 · 2** · 구현 위치: `server.py` 안의 호출 함수 하나

두 단계가 같은 함수로 모델을 부른다. 단계마다 다른 것은 **모델·프롬프트·max_tokens 뿐**이고, 나머지(헤더·타임아웃·재시도·오류 변환)는 여기서 한 번만 정한다.

## 모델은 `Models` 파일에서 읽는다

```
[Image API model]
dots-studio/dots-3-note-preview:free

[Text API model]
inclusionai/ling-3.0-flash-fin:free
```

- 모델 ID를 소스에 하드코딩하지 말 것. `Models`가 원본이다.
- `Models`는 git에서 제외되므로 **클론한 환경에는 없을 수 있다.** 없으면 `NO_KEY`와 같은 방식으로 화면에 안내한다 — 조용히 기본값으로 넘어가지 말 것.
- 파싱은 `tools/smoke_test_api.py`의 `read_models_file()`과 같은 규칙을 쓴다(형식이 이미 한 번 바뀐 전례가 있다).

## 호출 형식

OpenAI 호환이다. `urllib.request`로 충분하다 — `requests`를 끌어들이지 말 것.

```
POST https://openrouter.ai/api/v1/chat/completions
Authorization: Bearer <config.get_api_key()>
X-Title: fridge-recipe
```

## 단계별 파라미터

| | 1단계 (비전) | 2단계 (텍스트) |
|---|---|---|
| 모델 | `Models`의 image model | `Models`의 text model |
| `max_tokens` | **3000** | **1500** |
| `temperature` | 0 | 0.3 |
| `response_format` | json_schema (→ `UNIT_json_contract.md`) | 미지원 — 쓰지 말 것 |
| 타임아웃 | **60초** | 30초 |

`max_tokens` 3000의 근거: 실측에서 출력이 1,800~2,200 토큰이었다. **이 모델은 추론형이라 생각 과정이 출력 토큰을 먹는다.** 1,000으로 잡으면 최종 답이 비어서 돌아온다 — 실제로 300에서 그랬다.

## 응답 읽기 — 걸려 넘어지는 지점

```python
msg = payload["choices"][0]["message"]
content = (msg.get("content") or "").strip()
finish  = payload["choices"][0].get("finish_reason")
```

- **추론형 모델은 생각을 `message.reasoning`에, 최종 답을 `message.content`에 나눠 담는다.** `max_tokens`가 모자라면 `reasoning`만 차고 **`content`가 빈 문자열인 채로 끝난다.**
- 따라서 `content`가 비었다고 무조건 실패로 처리하지 말고 `finish_reason`을 함께 볼 것. `length`면 토큰 부족이고, `stop`인데도 비었으면 진짜 실패다.
- `reasoning`을 사용자에게 보여주지 말 것. 길고, 프롬프트 내용이 섞여 나온다.

## 재시도

| 상황 | 처리 |
|---|---|
| 429 (레이트리밋) | **자동 재시도하지 않는다.** 무료 모델이라 연타하면 더 오래 막힌다. 화면에 안내하고 사용자가 누르게 한다 |
| 5xx · 타임아웃 | 1회만 재시도, 2초 대기 |
| JSON 파싱 실패 | 1회 복구 시도 (→ `UNIT_json_contract.md`) |

재시도 횟수를 늘리지 말 것. 1단계가 한 번에 20초씩 걸리므로 3회 재시도는 사용자를 1분 넘게 기다리게 한다.

## 검증

- [ ] `Models`의 모델 ID를 바꾸면 JS를 고치지 않고도 다른 모델로 호출된다
- [ ] `Models`를 지우면 명확한 오류가 뜬다(조용한 기본값 없음)
- [ ] `max_tokens`를 300으로 낮추면 `finish_reason=length` + 빈 `content`가 재현된다
