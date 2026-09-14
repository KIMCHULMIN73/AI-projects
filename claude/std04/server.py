#!/usr/bin/env python3
"""냉장고 레시피 앱의 로컬 프록시. (→ units/UNIT_proxy.md)

    python3 server.py            # 8000번부터 빈 포트를 찾아 띄우고 브라우저를 연다
    python3 server.py 8080       # 포트 고정
    NO_OPEN=1 python3 server.py  # 브라우저 자동 실행 없이

**이 서버가 있는 유일한 이유는 키를 브라우저에서 숨기기 위해서다.** 저장·계산은
브라우저가 한다. 여기로 끌어오지 말 것.

키는 config.py로만 읽는다. 브라우저로 나가는 모든 JSON은 config.redact()를 통과한다.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import secrets
import shutil
import socket
import string
import subprocess
import sys
import threading
import time
import traceback
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parent
MODELS_FILE = ROOT / "Models"
API_URL = "https://openrouter.ai/api/v1/chat/completions"
APP_TITLE = "fridge-recipe"

HOST = "127.0.0.1"          # 0.0.0.0 금지 — 같은 망의 누구나 내 키로 API를 쓰게 된다
DEFAULT_PORT = 8000
PORT_SCAN = 20

# 프록시 폴더를 통째로 서빙하지 않는다. 여기엔 Models·config.py가 있고, 예전 방식의
# .env가 남아 있을 수도 있다. 화면에 필요한 파일만 이름으로 연다.
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/storage.js": ("storage.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}

# ── 오류 코드 (→ UNIT_proxy.md "오류 코드") ────────────────────────────────
# 한국어 문구는 여기서만 만든다. 화면은 옮기기만 한다(→ UNIT_ui_state.md).
ERRORS: dict[str, tuple[int, bool, str]] = {
    "NO_KEY": (503, False, "키가 설정되지 않았습니다. .env.example을 보세요."),
    "NO_MODEL": (503, False, "Models 파일에 모델이 적혀 있지 않습니다. CLAUDE.md의 모델 표를 보세요."),
    "RATE_LIMIT": (429, True, "무료 모델 사용량이 찼습니다. 잠시 후 다시 시도하세요."),
    "UPSTREAM": (502, True, "모델 서버가 응답하지 않습니다."),
    "TIMEOUT": (504, True, "시간이 너무 오래 걸립니다."),
    "BAD_OUTPUT": (502, True, "결과를 읽지 못했습니다. 다시 시도하세요."),
    "BAD_REQUEST": (400, False, "요청이 올바르지 않습니다."),
}


class ApiError(Exception):
    """브라우저에 `{"ok": false, "error": ...}`로 나갈 오류."""

    def __init__(self, code: str, message: str | None = None, *, retryable: bool | None = None,
                 log: str = ""):
        status, default_retry, default_msg = ERRORS[code]
        super().__init__(code)
        self.code = code
        self.status = status
        self.message = message or default_msg
        self.retryable = default_retry if retryable is None else retryable
        self.log = log          # 서버 터미널에만 남길 설명 (브라우저로 가지 않는다)


# ── Models 파일 (→ UNIT_model_call.md) ───────────────────────────────────

def read_models_file() -> dict[str, str]:
    """`Models`에서 모델 ID를 읽는다. 매 요청마다 읽으므로 서버를 다시 띄울 필요가 없다.

    규칙은 tools/smoke_test_api.py의 read_models_file()과 같다. 그쪽을 import하지
    않는 이유: tools/는 개발 전용이라 지워도 앱이 돌아야 한다. 형식을 바꾸면 두 곳을
    함께 고칠 것.

        [Image API model]                  |   image model : dots-studio/...
        dots-studio/dots-3-note-preview:free
    """
    out: dict[str, str] = {}
    if not MODELS_FILE.is_file():
        return out
    kind = None
    for raw in MODELS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip().strip("[]")
        if not line:
            continue
        lowered = line.lower()
        label, sep, tail = line.partition(":")
        if sep and "/" in tail and ("image" in label.lower() or "text" in label.lower()):
            out["image" if "image" in label.lower() else "text"] = tail.strip()
            continue
        if "/" in line:
            if kind:
                out[kind] = line
                kind = None
        elif "image" in lowered:
            kind = "image"
        elif "text" in lowered:
            kind = "text"
    return out


def require_model(kind: str) -> str:
    model = read_models_file().get(kind)
    if not model:
        raise ApiError("NO_MODEL", log=f"Models 파일({MODELS_FILE})에 {kind} 모델이 없음")
    return model


# ── OpenRouter 호출 — 두 단계가 함께 쓰는 함수 하나 (→ UNIT_model_call.md) ──

# 재시도·복구 호출을 시작하려면 최소 이만큼은 예산이 남아 있어야 한다.
# 그보다 적으면 거의 확실히 TIMEOUT으로 끝나므로 원래 오류를 그대로 돌려준다.
MIN_ATTEMPT_S = 5.0
RETRY_WAIT_S = 2.0


def _can_retry(deadline: float) -> bool:
    return deadline - time.monotonic() - RETRY_WAIT_S >= MIN_ATTEMPT_S


def call_model(model: str, messages: list, *, max_tokens: int, temperature: float,
               deadline: float, response_format: dict | None = None, extra: dict | None = None) -> dict:
    """한 번 호출하고 `{"content", "finish", "tokens_in", "tokens_out", "attempts"}`를 돌려준다.

    `deadline`은 time.monotonic() 기준의 **총 예산 끝**이다. 시도마다 새 타임아웃을
    주지 않는다(→ UNIT_model_call.md "타임아웃은 요청 하나의 총 예산이다").
    """
    body = {"model": model, "messages": messages, "max_tokens": max_tokens,
            "temperature": temperature}
    if response_format:
        body["response_format"] = response_format
    if extra:                      # reasoning·seed 등 단계별 고정값. 요청 본문에서 오는 값이 아니다.
        body.update(extra)
    data = json.dumps(body).encode()

    attempts = 0
    while True:
        attempts += 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ApiError("TIMEOUT", log="예산 소진")
        try:
            headers = config.auth_header(**{"X-Title": APP_TITLE})
        except config.MissingKeyError as exc:
            raise ApiError("NO_KEY", log=config.redact(exc).splitlines()[0]) from None
        req = urllib.request.Request(API_URL, data=data, headers=headers, method="POST")

        retry_reason = ""
        try:
            with urllib.request.urlopen(req, timeout=remaining) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()          # 오류 본문은 읽지 않는다 — 헤더가 섞여 나올 수 있다
            if status == 429:
                raise ApiError("RATE_LIMIT", log="HTTP 429") from None
            if status in (401, 403):
                raise ApiError("NO_KEY", "키가 거부되었습니다(폐기되었거나 잘못된 키). "
                               "python3 tools/check_secrets.py --online 으로 확인하세요.",
                               log=f"HTTP {status}") from None
            if status < 500:
                raise ApiError("UPSTREAM", f"모델 서버가 요청을 거부했습니다 (HTTP {status}).",
                               retryable=False, log=f"HTTP {status}") from None
            retry_reason = f"HTTP {status}"
        except (TimeoutError, socket.timeout):
            raise ApiError("TIMEOUT", log=f"{attempts}번째 시도에서 시간 초과") from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise ApiError("TIMEOUT", log=f"{attempts}번째 시도에서 시간 초과") from None
            retry_reason = f"연결 실패: {config.redact(exc.reason)}"
        except (ValueError, OSError) as exc:          # JSON이 아닌 본문, 끊긴 연결
            retry_reason = f"응답 본문 오류: {type(exc).__name__}"
        else:
            # OpenRouter는 200 안에 {"error": ...}를 담아 보내기도 한다(제공자 쪽 실패).
            err = payload.get("error") if isinstance(payload, dict) else None
            if err:
                code = err.get("code") if isinstance(err, dict) else None
                if code == 429:
                    raise ApiError("RATE_LIMIT", log="본문 error.code=429")
                retry_reason = f"본문 error.code={code}"
            else:
                try:
                    choice = payload["choices"][0]
                    msg = choice["message"]
                except (KeyError, IndexError, TypeError):
                    retry_reason = "choices 없음"
                else:
                    usage = payload.get("usage") or {}
                    # reasoning은 읽지 않는다. 사용자에게 보여줄 것이 아니다.
                    return {
                        "content": (msg.get("content") or "").strip(),
                        "finish": choice.get("finish_reason"),
                        "tokens_in": int(usage.get("prompt_tokens") or 0),
                        "tokens_out": int(usage.get("completion_tokens") or 0),
                        "attempts": attempts,
                    }

        # 여기 왔으면 5xx 성격의 실패다. 1회만, 예산 안에서만 다시 시도한다.
        if attempts >= 2 or not _can_retry(deadline):
            raise ApiError("UPSTREAM", log=f"{retry_reason} (시도 {attempts}회)")
        log(f"  재시도: {retry_reason}")
        time.sleep(RETRY_WAIT_S)


# ── JSON 받아내기 (→ UNIT_json_contract.md) ──────────────────────────────

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S | re.I)


def parse_json_lenient(text: str):
    """1) 그대로  2) 코드펜스 벗기고  3) 첫 '{'~마지막 '}'. 전부 실패하면 None."""
    candidates = [text]
    m = _FENCE_RE.match(text)
    if m:
        candidates.append(m.group(1))
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for c in candidates:
        try:
            return json.loads(c)
        except ValueError:
            continue
    return None


REPAIR_PROMPT = "이건 JSON이 아니다. 설명 없이 JSON만 다시 출력하라."


def ask_for_json(model: str, messages: list, *, shape_ok, deadline: float, **params) -> tuple[dict, dict]:
    """모델을 불러 JSON을 받는다. 실패하면 이전 출력을 붙여 **1회 복구**한다.

    `(파싱된 객체, meta)`를 돌려준다. 두 번 다 못 읽으면 BAD_OUTPUT.
    """
    meta = {"tokens_in": 0, "tokens_out": 0, "attempts": 0, "repaired": False}

    def once(msgs):
        r = call_model(model, msgs, deadline=deadline, **params)
        meta["tokens_in"] += r["tokens_in"]
        meta["tokens_out"] += r["tokens_out"]
        meta["attempts"] += r["attempts"]
        meta["finish_reason"] = r["finish"]
        return r

    r = once(messages)
    if not r["content"]:
        # 추론형 모델: finish=length면 생각만 하다 토큰이 떨어진 것이다.
        # 복구 요청에 붙일 이전 출력이 없으므로 복구하지 않는다.
        why = "max_tokens 부족(finish=length)" if r["finish"] == "length" else f"빈 content(finish={r['finish']})"
        raise ApiError("BAD_OUTPUT", log=why)

    parsed = parse_json_lenient(r["content"])
    if shape_ok(parsed):
        return parsed, meta

    if not _can_retry(deadline):
        raise ApiError("BAD_OUTPUT", log=f"파싱 실패, 복구할 예산 없음: {config.redact(r['content'][:200])!r}")
    log(f"  복구 시도: 파싱 실패 {config.redact(r['content'][:120])!r}")
    meta["repaired"] = True
    repair = messages + [{"role": "assistant", "content": r["content"]},
                         {"role": "user", "content": REPAIR_PROMPT}]
    r = once(repair)
    parsed = parse_json_lenient(r["content"]) if r["content"] else None
    if shape_ok(parsed):
        return parsed, meta
    raise ApiError("BAD_OUTPUT", log=f"복구도 실패: {config.redact(r['content'][:200])!r}")


# ── 1단계: 이미지 → 식재료 (→ PRD_step1.md) ──────────────────────────────

# 프롬프트에 박는 목록이자 화면 그룹의 순서. 늘리려면 프롬프트·값 검증(여기)·화면(app.js)을 함께.
CATEGORIES = ["채소", "과일", "육류", "해산물", "유제품", "달걀", "곡물면", "양념소스", "가공식품", "음료", "기타"]
CONFIDENCE = ["high", "medium", "low"]

# max_tokens 6000: 실제 냉장고 사진에서 출력이 3,110~4,615 토큰이었다(추론이 대부분).
VISION_PARAMS = {"max_tokens": 6000, "temperature": 0}
VISION_BUDGET_S = 60

VISION_PROMPT = """냉장고 사진이다. 보이는 식재료를 빠짐없이 찾아라.
- name_ko: 한국어 식재료 이름 하나만 적어라. 괄호·영어·슬래시(/)·설명을 붙이지 마라.
  포장에 영어가 적혀 있어도 한국어로 옮겨라 (EGGS→달걀).
- 한 항목에는 재료 하나만 넣어라. '간장, 고추장'처럼 묶지 말고 따로 적어라.
- category: 포장의 글자가 아니라 그 식품이 실제로 무엇인지로 분류하라.
  달걀은 '달걀', 우유·치즈·요구르트는 '유제품', 두부는 '가공식품',
  김치는 '가공식품'이다. '양념소스'는 간장·고추장·식용유처럼 조미료일 때만 쓴다.
- quantity: 개수나 용량이 보이면 '6개', '1L'처럼 짧게, 없으면 '미상'. 위치는 적지 마라.
- 둘 중 무엇인지 모르거나 확실하지 않은 것은 items에 넣지 말고
  unsure에 한국어 한 문장으로 이유와 함께 적어라.
- 오래 고민하지 말고 보이는 대로 빠르게 답하라.

반드시 아래 JSON만 출력하라. 설명·인사말·마크다운 코드펜스 금지.
category는 """ + "·".join(CATEGORIES) + """ 중 하나, confidence는 high·medium·low 중 하나.
{"items":[{"name_ko":"","category":"","quantity":"","confidence":""}],"unsure":[""]}"""
# 지금 비전 모델(ling-3.0-flash-vl)은 response_format을 지원하지 않는다. 스키마 대신
# 빈 값이 든 형태 예시를 프롬프트에 박고, 파싱은 관대하게·복구 1회로 받는다(→ UNIT_json_contract.md).
# enum도 강제되지 않으므로 to_ingredient_list()의 값 검증이 유일한 벽이다.

MAX_IMAGE_CHARS = 4 * 1024 * 1024                 # 전송 후 상한 4MB (→ UNIT_image_input.md)
MAX_BODY_BYTES = MAX_IMAGE_CHARS + 1024
_DATA_URI_RE = re.compile(r"^data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=\s]+)$")

NAME_MAX, LIST_MAX, UNSURE_MAX = 40, 100, 200

# 이름 수상함 판정 (→ UNIT_json_contract.md "값 검증")
_NAME_ALLOWED_RE = re.compile(r"^[가-힣A-Za-z0-9\s()]*$")
_NAME_STUTTER_RE = re.compile(r"([가-힣])\1\1")                 # 같은 음절 3연속
_NAME_GLUED_RE = re.compile(r"[가-힣][A-Za-z]|[A-Za-z][가-힣]")  # 공백 없이 한글·영문이 붙음


def name_is_suspicious(name: str) -> bool:
    return (not name
            or not _NAME_ALLOWED_RE.match(name)
            or bool(_NAME_STUTTER_RE.search(name))
            or bool(_NAME_GLUED_RE.search(name)))


def _clean(value, limit: int) -> tuple[str, bool]:
    """문자열로 만들고 공백을 정리해 자른다. `(값, 잘렸는가)`."""
    text = unicodedata.normalize("NFC", str(value if value is not None else ""))
    text = " ".join(text.split())
    return text[:limit], len(text) > limit


def to_ingredient_list(raw: dict) -> dict:
    """모델 출력(스키마 통과본)을 `IngredientList` 계약으로 옮기면서 값을 검증한다.

    스키마 통과 ≠ 쓸 수 있는 값. 수상한 항목은 **버리지 않고** needs_review를 붙인다.
    """
    items = []
    for it in (raw.get("items") or [])[:LIST_MAX]:
        if not isinstance(it, dict):
            continue
        name, cut = _clean(it.get("name_ko", it.get("name")), NAME_MAX)
        quantity, _ = _clean(it.get("quantity"), NAME_MAX)
        category = it.get("category")
        confidence = it.get("confidence")
        review = False
        if category not in CATEGORIES:
            category, review = "기타", True
        if confidence not in CONFIDENCE:
            confidence, review = "low", True
        if cut or name_is_suspicious(name):
            confidence, review = "low", True
        items.append({
            "name": name,
            "category": category,
            "quantity": quantity or "미상",
            "confidence": confidence,
            "needs_review": review,
            "source": "vision",
        })

    unsure = []
    for u in (raw.get("unsure") or [])[:LIST_MAX]:
        text, _ = _clean(u, UNSURE_MAX)
        if text:
            unsure.append(text)

    return {
        "items": items,
        "unsure": unsure,
        "analyzed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _vision_shape_ok(parsed) -> bool:
    return isinstance(parsed, dict) and isinstance(parsed.get("items"), list)


def handle_vision(body: dict) -> tuple[dict, dict]:
    image = body.get("image") if isinstance(body, dict) else None
    if not isinstance(image, str) or not image:
        raise ApiError("BAD_REQUEST", "사진이 비어 있습니다.")
    if len(image) > MAX_IMAGE_CHARS:
        raise ApiError("BAD_REQUEST", "사진이 너무 큽니다(4MB 초과). 더 작은 사진을 올려 주세요.")
    m = _DATA_URI_RE.match(image)
    if not m:
        raise ApiError("BAD_REQUEST", "이미지 형식이 아닙니다. JPEG·PNG·WebP 사진을 올려 주세요.")
    try:
        base64.b64decode(m.group(2), validate=False)
    except (binascii.Error, ValueError):
        raise ApiError("BAD_REQUEST", "이미지 데이터가 손상되었습니다.") from None

    model = require_model("image")          # 키보다 먼저: 둘 다 없으면 Models부터 안내
    deadline = time.monotonic() + VISION_BUDGET_S
    messages = [{"role": "user", "content": [
        {"type": "text", "text": VISION_PROMPT},
        {"type": "image_url", "image_url": {"url": image}},
    ]}]
    raw, meta = ask_for_json(model, messages, shape_ok=_vision_shape_ok, deadline=deadline,
                             **VISION_PARAMS)
    return to_ingredient_list(raw), meta


# ── 2단계: 식재료 → 레시피 (→ PRD_step2.md) ──────────────────────────────

# 텍스트 모델도 추론형이다. 1500이면 매번 잘렸고, 옵션 없이는 3,000~6,000토큰을 넘나들었다.
# effort=low로 8.5~9.6초·약 3,000토큰(2026-09-14 실측). 추론을 끄면 빠르지만 프롬프트 규칙을 어겼다.
RECIPE_PARAMS = {"max_tokens": 8000, "temperature": 0.3}
RECIPE_REASONING = {"effort": "low"}
RECIPE_BUDGET_S = 30
RECIPE_COUNTS = (2, 3)
DIFFICULTY = ["쉬움", "보통", "어려움"]

# PRD_step2.md "프롬프트" 그대로. {count} 자리만 바꾼다(JSON 중괄호 때문에 format을 쓰지 않는다).
RECIPE_RULES = """
이 재료로 만들 요리 {count}개를 추천하라.

규칙:
- 위 재료를 최대한 쓰고, 꼭 필요한 추가 재료만 missing에 적어라.
- 소금·후추·식용유·물은 어느 집에나 있다고 보고 missing에 넣지 마라.
- uses에는 위 재료 목록의 이름을 그대로 써라. 바꿔 쓰지 마라.
- steps는 한 문장씩, 5~8단계로.

반드시 아래 JSON만 출력하라. 설명·인사말·마크다운 코드펜스 금지.
{"recipes":[{"title":"","summary":"","time_minutes":0,"difficulty":"쉬움",
"servings":2,"uses":[""],"missing":[""],"steps":[""],"tips":""}]}"""

TITLE_MAX, SUMMARY_MAX, STEP_MAX, TIPS_MAX, STEPS_MAX = 60, 120, 300, 300, 20


def read_recipe_request(body) -> tuple[list[dict], int]:
    """`{"ingredients": [{"name", "quantity"}], "count"}`만 받는다. 그 밖의 필드는 무시한다.

    IngredientList를 통째로 보내도 confidence·source 같은 1단계 사정은 모델에게 가지 않는다.
    """
    if not isinstance(body, dict) or not isinstance(body.get("ingredients"), list):
        raise ApiError("BAD_REQUEST", "재료 목록이 없습니다.")
    count = body.get("count", 3)
    if isinstance(count, bool) or count not in RECIPE_COUNTS:
        raise ApiError("BAD_REQUEST", "추천 개수는 2 또는 3이어야 합니다.")
    seen, ingredients = set(), []
    for it in body["ingredients"][:LIST_MAX]:
        if not isinstance(it, dict):
            continue
        name, _ = _clean(it.get("name"), NAME_MAX)
        if not name or name in seen:
            continue
        quantity, _ = _clean(it.get("quantity"), NAME_MAX)
        seen.add(name)
        ingredients.append({"name": name, "quantity": "" if quantity == "미상" else quantity})
    if not ingredients:
        raise ApiError("BAD_REQUEST", "재료를 하나 이상 넣어 주세요.")
    return ingredients, int(count)


def recipe_prompt(ingredients: list[dict], count: int) -> str:
    listed = ", ".join(f"{i['name']} {i['quantity']}".strip() for i in ingredients)
    return "재료: " + listed + RECIPE_RULES.replace("{count}", str(count))


def _int_in(value, lo: int, hi: int) -> int | None:
    """`20`, `20.0`, `"20분"`을 20으로. 범위 밖이거나 숫자가 없으면 None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = int(value)
    else:
        m = re.search(r"\d+", str(value or ""))
        if not m:
            return None
        n = int(m.group())
    return n if lo <= n <= hi else None


def _str_list(value, max_items: int, max_chars: int, *, dedupe: bool = True) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for v in value[:max_items]:
        text, _ = _clean(v, max_chars)
        if text and not (dedupe and text in out):
            out.append(text)
    return out


def _recipe_id_prefix(now: datetime) -> str:
    # 초 단위 시각 + 요청마다 무작위 4자. 같은 초에 두 번 눌러도 겹치지 않는다(→ PRD_step2 `id`).
    rand = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(4))
    return f"r-{now:%Y%m%d%H%M%S}-{rand}"


def to_recipes(raw: dict, count: int) -> dict:
    """모델 출력을 `Recipe` 계약으로 옮기면서 값의 형태를 검증한다.

    정렬과 `uses`→`missing` 옮기기는 하지 않는다 — 그건 화면의 일이다(→ UNIT_proxy).
    """
    now = datetime.now().astimezone()
    prefix = _recipe_id_prefix(now)
    recipes = []
    for rc in raw.get("recipes") or []:
        if len(recipes) >= count:
            break
        if not isinstance(rc, dict):
            continue
        title, _ = _clean(rc.get("title"), TITLE_MAX)
        steps = _str_list(rc.get("steps"), STEPS_MAX, STEP_MAX, dedupe=False)
        if not title or not steps:            # 이름이나 조리 순서가 없으면 쓸 수 없다
            continue
        recipes.append({
            "id": f"{prefix}-{len(recipes) + 1}",
            "title": title,
            "summary": _clean(rc.get("summary"), SUMMARY_MAX)[0],
            "time_minutes": _int_in(rc.get("time_minutes"), 1, 600),
            "difficulty": rc.get("difficulty") if rc.get("difficulty") in DIFFICULTY else "보통",
            "servings": _int_in(rc.get("servings"), 1, 20),
            "uses": _str_list(rc.get("uses"), LIST_MAX, NAME_MAX),
            "missing": _str_list(rc.get("missing"), LIST_MAX, NAME_MAX),
            "steps": steps,
            "tips": _clean(rc.get("tips"), TIPS_MAX)[0],
            "generated_at": now.isoformat(timespec="seconds"),
        })
    if not recipes:
        raise ApiError("BAD_OUTPUT", log="쓸 수 있는 레시피가 없음(제목·조리 순서 누락)")
    return {"recipes": recipes}


def _recipe_shape_ok(parsed) -> bool:
    return isinstance(parsed, dict) and isinstance(parsed.get("recipes"), list) and bool(parsed["recipes"])


def handle_recipe(body: dict) -> tuple[dict, dict]:
    ingredients, count = read_recipe_request(body)
    model = require_model("text")
    deadline = time.monotonic() + RECIPE_BUDGET_S
    # seed는 요청마다 새로 뽑는다. 같은 재료로 다시 눌러도 다른 요리가 나올 수 있게(→ PRD_step2 "프롬프트").
    seed = secrets.randbelow(2**31 - 1) + 1
    messages = [{"role": "user", "content": recipe_prompt(ingredients, count)}]
    raw, meta = ask_for_json(model, messages, shape_ok=_recipe_shape_ok, deadline=deadline,
                             extra={"reasoning": RECIPE_REASONING, "seed": seed}, **RECIPE_PARAMS)
    meta["seed"] = seed
    return to_recipes(raw, count), meta


# ── HTTP ────────────────────────────────────────────────────────────────

def log(line: str) -> None:
    print(config.redact(line), file=sys.stderr, flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "fridge-proxy"
    sys_version = ""

    # 기본 구현은 stderr에 그대로 쓴다. 키가 섞일 일은 없지만 규칙대로 가린다.
    def log_message(self, fmt, *args):
        log(f"{self.address_string()} - {fmt % args}")

    # ── 공통 ──
    def _host_ok(self) -> bool:
        """DNS 리바인딩 방어. 다른 사이트가 자기 도메인을 127.0.0.1로 돌려 이 프록시를
        부르면 Host 헤더가 그 도메인이 된다. 우리 주소가 아니면 거절한다."""
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data: blob:; "
                         "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj: dict) -> None:
        # 나가는 모든 문자열을 redact한다. 키 패턴이 JSON 구문을 깨는 문자를 만들지는 않는다.
        text = config.redact(json.dumps(obj, ensure_ascii=False))
        self._send(status, text.encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, err: ApiError) -> None:
        self._json(err.status, {"ok": False, "error": {
            "code": err.code, "message": err.message, "retryable": err.retryable}})

    # ── GET ──
    def do_GET(self):
        if not self._host_ok():
            return self._error(ApiError("BAD_REQUEST", "허용되지 않은 주소입니다."))
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            return self._health()
        if path in STATIC_FILES:
            name, ctype = STATIC_FILES[path]
            try:
                body = (ROOT / name).read_bytes()
            except OSError:
                return self._send(404, f"{name} 가 없습니다.".encode(), "text/plain; charset=utf-8")
            return self._send(200, body, ctype)
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def _health(self):
        try:
            models = read_models_file()
            if not models.get("image") or not models.get("text"):
                raise ApiError("NO_MODEL")
            try:
                config.get_api_key(quiet=True)    # 읽을 수 있는지만 본다. 값은 담지 않는다.
            except config.MissingKeyError:
                raise ApiError("NO_KEY") from None
        except ApiError as err:
            return self._error(err)
        self._json(200, {"ok": True, "models": {"vision": models["image"], "text": models["text"]}})

    # ── POST ──
    def do_POST(self):
        if not self._host_ok():
            return self._error(ApiError("BAD_REQUEST", "허용되지 않은 주소입니다."))
        path = self.path.split("?", 1)[0]
        routes = {"/api/vision": handle_vision, "/api/recipe": handle_recipe}
        if path not in routes:
            return self._send(404, b"not found", "text/plain; charset=utf-8")

        started = time.monotonic()
        try:
            body = self._read_json_body()
            data, meta = routes[path](body)
        except ApiError as err:
            log(f"[{path}] {err.code} {err.log}".rstrip())
            return self._error(err)
        except Exception:
            log(f"[{path}] 내부 오류\n{traceback.format_exc()}")
            return self._error(ApiError("UPSTREAM", "처리 중 문제가 생겼습니다. 다시 시도하세요."))

        meta = {"elapsed_ms": int((time.monotonic() - started) * 1000), **meta}
        log(f"[{path}] ok {meta}")
        self._json(200, {"ok": True, "data": data, "meta": meta})

    def _read_json_body(self) -> dict:
        # application/json이 아니면 거절한다. 다른 사이트가 <form>·text/plain으로 보내는
        # "단순 요청"은 CORS 사전 확인 없이 도착하므로, 이것이 내 무료 한도를 지키는 벽이다.
        ctype = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            raise ApiError("BAD_REQUEST", "JSON으로 보내야 합니다.")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise ApiError("BAD_REQUEST", "본문 길이가 없습니다.") from None
        if length > MAX_BODY_BYTES:
            self.close_connection = True           # 남은 본문을 읽지 않고 끊는다
            raise ApiError("BAD_REQUEST", "사진이 너무 큽니다(4MB 초과). 더 작은 사진을 올려 주세요.")
        try:
            return json.loads(self.rfile.read(length))
        except ValueError:
            raise ApiError("BAD_REQUEST", "본문이 JSON이 아닙니다.") from None


# ── 실행 ────────────────────────────────────────────────────────────────

class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False     # 다른 프로세스가 쓰는 포트를 가로채지 않는다


def bind(requested: str | None) -> Server:
    if requested:
        try:
            return Server((HOST, int(requested)), Handler)
        except ValueError:
            sys.exit(f"포트는 숫자여야 합니다: {requested}")
        except OSError:
            print(f"포트 {requested}번은 이미 다른 프로그램이 쓰고 있습니다.", file=sys.stderr)
            print(f"  사용 중인 프로세스 확인:  ss -ltnp | grep :{requested}", file=sys.stderr)
            print(f"  또는 다른 포트로 실행:    python3 server.py {int(requested) + 1}", file=sys.stderr)
            sys.exit(1)
    for port in range(DEFAULT_PORT, DEFAULT_PORT + PORT_SCAN + 1):
        try:
            srv = Server((HOST, port), Handler)
        except OSError:
            continue
        if port != DEFAULT_PORT:
            print(f"※ {DEFAULT_PORT}번 포트가 사용 중이라 {port}번으로 실행합니다.\n")
        return srv
    print(f"{DEFAULT_PORT}~{DEFAULT_PORT + PORT_SCAN}번 포트가 모두 사용 중입니다. "
          f"python3 server.py <포트> 로 지정하세요.", file=sys.stderr)
    sys.exit(1)


def startup_report() -> None:
    models = read_models_file()
    for kind in ("image", "text"):
        print(f"  {kind:5} 모델 : {models.get(kind) or '!! Models 파일에 없음'}")
    try:
        _key, source, warnings = config.find_api_key()
        print(f"  키 출처    : {source}")          # 키 자체는 찍지 않는다
        for w in warnings:
            print(f"  [경고] {config.redact(w)}")
    except config.MissingKeyError:
        print("  키 출처    : !! 찾지 못함 — .env.example 을 보세요 (화면에도 안내됩니다)")


def main() -> None:
    # 출력을 파일·파이프로 돌려도 주소가 곧바로 보이게 한다(기본은 블록 버퍼라 한참 비어 있다).
    sys.stdout.reconfigure(line_buffering=True)
    srv = bind(sys.argv[1] if len(sys.argv) > 1 else None)
    host, port = srv.server_address[:2]
    url = f"http://{host}:{port}/"
    print("냉장고 사진으로 레시피 추천 — 로컬 프록시")
    print(f"  주소       : {url}   (127.0.0.1에만 열림, Ctrl+C로 종료)")
    startup_report()
    print()

    if (not os.environ.get("NO_OPEN") and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
            and shutil.which("xdg-open")):
        threading.Timer(1.0, lambda: subprocess.Popen(
            ["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)).start()
        print("브라우저를 여는 중입니다... (안 열리면 위 주소를 직접 입력하세요)\n")

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
