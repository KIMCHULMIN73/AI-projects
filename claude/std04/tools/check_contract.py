#!/usr/bin/env python3
"""프록시 계약 점검 — 가짜 모델로 server.py를 직접 부른다. 개발 전용, 네트워크·API 키를 쓰지 않는다.

    python3 tools/check_contract.py

화면이 아니라 프록시가 지키기로 한 약속을 본다 (→ UNIT_json_contract · UNIT_model_call · UNIT_proxy):

  1. 순수 함수    관대한 JSON 파싱, 이름 수상함 판정, IngredientList 값 검증
  2. /api/vision  실제 HTTP로 부르되 모델 호출(call_model)만 가짜로 바꾼다
  3. /api/recipe  같은 방식
  4. call_model   OpenRouter 응답(urlopen)을 가짜로 바꿔 재시도·예산·오류 변환을 본다
  5. 보안         모델 출력에 키 모양 문자열이 섞여도 응답·로그에서는 가려진다

실제 모델의 출력 품질(uses 이름 일치·기본 양념 제외 등)은 여기서 보지 않는다 — check_browser_step2.py의 실제 모드가 본다.
종료코드는 모두 통과면 0, 하나라도 실패면 1.
"""

from __future__ import annotations

import base64
import http.client
import io
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS.parent))
sys.path.insert(0, str(TOOLS))
import config  # noqa: E402
import server as s  # noqa: E402
from browser_cdp import Checker, free_port  # noqa: E402

# 키 모양의 가짜 문자열. 소스에 한 덩어리로 적지 않는다 — check_secrets.py가 키로 오인하지 않게.
FAKE_KEY = "sk-or-" + "v1-" + "abcd" * 12
REDACTED_PREFIX = FAKE_KEY[: len("sk-or-v1-") + 4] + "…"

IMG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0 fake jpeg").decode()
ISO_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")


# ── 가짜 모델을 끼운 실제 HTTP 서버 ──────────────────────────────────────

class Harness:
    """server.Handler를 빈 포트에 띄우고, call_model만 정해진 출력을 돌려주는 가짜로 바꾼다."""

    def __init__(self, *, quiet: bool = True):
        self.queue: list[tuple[str, str]] = []
        self.calls: list[dict] = []
        self._orig = (s.call_model, s.require_model, s.log)
        s.call_model = self._fake
        s.require_model = lambda kind: f"fake/{kind}"
        if quiet:
            s.log = lambda line: None
        self.srv = ThreadingHTTPServer(("127.0.0.1", free_port()), s.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def _fake(self, model, messages, **kw):
        self.calls.append({"model": model, "messages": messages, **kw})
        content, finish = self.queue.pop(0)
        return {"content": content, "finish": finish, "tokens_in": 10, "tokens_out": 20, "attempts": 1}

    def post(self, path: str, payload=None, outputs=(), *, content_type: str = "application/json", raw: bytes | None = None,
             headers: dict | None = None):
        """`(상태코드, 본문 JSON, 본문 원문)`"""
        self.queue[:] = list(outputs)
        self.calls.clear()
        data = raw if raw is not None else json.dumps(payload, ensure_ascii=False).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                     headers={"Content-Type": content_type, **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                status, text = r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            status, text = e.code, e.read().decode()
        return status, json.loads(text), text

    def post_declared_length(self, path: str, length: int):
        """Content-Length만 크게 적고 본문은 보내지 않는다 — 서버가 본문을 읽기 전에 거절하는지 본다."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.putrequest("POST", path)
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(length))
        conn.endheaders()
        r = conn.getresponse()
        body = json.loads(r.read().decode())
        conn.close()
        return r.status, body

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
        s.call_model, s.require_model, s.log = self._orig


def item(name, category="달걀", quantity="6개", confidence="high"):
    return {"name_ko": name, "category": category, "quantity": quantity, "confidence": confidence}


def vision_out(*items, unsure=()):
    return json.dumps({"items": list(items), "unsure": list(unsure)}, ensure_ascii=False)


def recipe(**kw):
    base = {"title": "김치 두부 계란찜", "summary": "", "time_minutes": 20, "difficulty": "쉬움", "servings": 2,
            "uses": ["달걀"], "missing": [], "steps": ["썬다", "찐다"], "tips": ""}
    return {**base, **kw}


def recipe_out(*recipes):
    return json.dumps({"recipes": list(recipes)}, ensure_ascii=False)


# ── 1. 순수 함수 ────────────────────────────────────────────────────────

def check_pure(c: Checker) -> None:
    print("\n── 1. 순수 함수 (UNIT_json_contract)")
    cases = [('{"items":[]}', {"items": []}), ('```json\n{"a":1}\n```', {"a": 1}), ('```\n{"a":2}\n```', {"a": 2}),
             ('네, 결과입니다:\n{"a":3} 끝', {"a": 3}), ("JSON 아님", None), ("", None)]
    got = [(t, s.parse_json_lenient(t)) for t, _ in cases]
    c.check("관대한 파싱: 그대로 → 코드펜스 벗기기 → 첫 '{'~마지막 '}' → 못 읽으면 None",
            all(g == want for (_, g), (_, want) in zip(got, cases)),
            "; ".join(f"{t[:12]!r}→{g}" for t, g in got))

    names = {"달modification달걀": True, "달달달걀": True, "우유ㅋ": True, "Greek요거트": True, "": True,
             "토마토": False, "바나나": False, "KIRKLAND 우유": False, "달걀": False, "우유(저지방)": False, "파프리카 2": False}
    wrong = {n: s.name_is_suspicious(n) for n, want in names.items() if s.name_is_suspicious(n) != want}
    c.check("이름 판정: 허용 문자 밖·같은 음절 3연속·공백 없이 한글+영문 붙음은 수상 / 떨어진 반복·공백 띈 영문은 정상",
            not wrong, f"{len(names)}개 중 어긋남 {wrong}")

    raw = {"items": [item("달modification달걀"), item("  우유 ", "야채", ""), item("가" * 50, "채소", "1", "sure"), "쓰레기",
                     item("두부", "가공식품", "2모", "medium")]
                    + [item(f"재료{i}", "기타", "1") for i in range(120)],
           "unsure": ["초록 병", "  "]}
    out = s.to_ingredient_list(raw)
    it = out["items"]
    c.check("IngredientList 값 검증: 깨진 이름은 버리지 않고 low+needs_review, 목록 밖 카테고리는 기타+needs_review",
            it[0] == {"name": "달modification달걀", "category": "달걀", "quantity": "6개", "confidence": "low", "needs_review": True, "source": "vision"}
            and it[1] == {"name": "우유", "category": "기타", "quantity": "미상", "confidence": "high", "needs_review": True, "source": "vision"},
            f"깨진 이름 → {it[0]}; '야채'·빈 수량·앞뒤 공백 → {it[1]}")
    c.check("IngredientList 값 검증: 이름 40자·배열 100개로 자르고, 객체가 아닌 항목·빈 unsure는 버린다",
            len(it[2]["name"]) == s.NAME_MAX and it[2]["needs_review"] and it[3]["name"] == "두부" and not it[3]["needs_review"]
            and len(it) <= s.LIST_MAX and out["unsure"] == ["초록 병"] and ISO_RE.match(out["analyzed_at"]),
            f"50자 이름 → {len(it[2]['name'])}자·review={it[2]['needs_review']}; '쓰레기' 다음 항목={it[3]['name']}; "
            f"125개 입력 → {len(it)}개; unsure={out['unsure']}; analyzed_at={out['analyzed_at']}")


# ── 2. /api/vision ──────────────────────────────────────────────────────

def check_vision(c: Checker, h: Harness) -> None:
    print("\n── 2. /api/vision (UNIT_json_contract · UNIT_proxy)")
    st, b, _ = h.post("/api/vision", {"image": IMG}, [(vision_out(item("달걀")), "stop")])
    call = h.calls[0] if h.calls else {}
    content = (call.get("messages") or [{}])[0].get("content") or [{}, {}]
    text, image = content[0].get("text", ""), content[1].get("image_url", {}).get("url")
    c.check("정상 응답: IngredientList + meta, 모델 요청은 max_tokens 6000·temperature 0·response_format 없음",
            st == 200 and b["ok"] and b["data"]["items"][0]["name"] == "달걀" and {"elapsed_ms", "tokens_in", "tokens_out"} <= set(b["meta"])
            and call.get("max_tokens") == 6000 and call.get("temperature") == 0 and call.get("response_format") is None and call.get("model") == "fake/image",
            f"http={st}, meta={b.get('meta')}, 요청 파라미터={ {k: call.get(k) for k in ('model', 'max_tokens', 'temperature', 'response_format')} }")
    c.check("프롬프트 끝에 JSON 형태 예시와 허용 카테고리가 붙고, 사진 data URI는 그대로 넘어간다",
            '{"items":[{"name_ko":""' in text and "·".join(s.CATEGORIES) in text and image == IMG,
            f"형태 예시 포함={'{\"items\":[{\"name_ko\":\"\"' in text}, 카테고리 목록 포함={'·'.join(s.CATEGORIES) in text}, image 동일={image == IMG}")

    st, b, _ = h.post("/api/vision", {"image": IMG}, [("```json\n" + vision_out(item("우유", "유제품")) + "\n```", "stop")])
    fence = (st, b["ok"], len(h.calls))
    st, b, _ = h.post("/api/vision", {"image": IMG}, [("결과입니다!\n" + vision_out(item("두부", "가공식품")) + "\n끝", "stop")])
    greet = (st, b["ok"], len(h.calls))
    c.check("모델이 코드펜스를 붙이거나 앞뒤에 인사말을 붙여도 복구 호출 없이 파싱된다", fence == greet == (200, True, 1),
            f"펜스 → http={fence[0]} ok={fence[1]} 호출 {fence[2]}회; 인사말 → http={greet[0]} ok={greet[1]} 호출 {greet[2]}회")

    st, b, _ = h.post("/api/vision", {"image": IMG}, [("JSON이 아닙니다", "stop"), (vision_out(item("달걀")), "stop")])
    msgs = h.calls[1]["messages"] if len(h.calls) > 1 else []
    c.check("1회 실패 → 같은 요청 반복이 아니라 이전 출력을 붙여 복구 1회 → 성공",
            st == 200 and len(h.calls) == 2 and len(msgs) == 3 and msgs[1] == {"role": "assistant", "content": "JSON이 아닙니다"}
            and msgs[2] == {"role": "user", "content": s.REPAIR_PROMPT} and b["meta"]["repaired"],
            f"http={st}, 호출 {len(h.calls)}회, 복구 메시지={[m['role'] for m in msgs]}, repaired={b.get('meta', {}).get('repaired')}")

    st, b, _ = h.post("/api/vision", {"image": IMG}, [("아님", "stop"), ("또 아님", "stop")])
    fail = (st, b.get("error"), len(h.calls))
    st2, b2, _ = h.post("/api/vision", {"image": IMG}, [(vision_out(item("달걀")), "stop")])
    c.check("2회 연속 실패 → BAD_OUTPUT(재시도 가능), 서버는 살아 있어 다음 요청은 정상",
            fail[1] and fail[1]["code"] == "BAD_OUTPUT" and fail[1]["retryable"] and fail[0] == 502 and fail[2] == 2 and b2["ok"],
            f"http={fail[0]} {fail[1]}, 호출 {fail[2]}회 → 다음 요청 http={st2} ok={b2['ok']}")

    st, b, _ = h.post("/api/vision", {"image": IMG}, [('{"foo": 1}', "stop"), ('{"bar": 2}', "stop")])
    shape = (st, b.get("error", {}).get("code"), len(h.calls))
    st, b, _ = h.post("/api/vision", {"image": IMG}, [("", "length")])
    empty = (st, b.get("error", {}).get("code"), len(h.calls))
    c.check("JSON이어도 items가 없으면 실패로 보고 복구, 빈 content+finish=length면 복구 없이 BAD_OUTPUT",
            shape == (502, "BAD_OUTPUT", 2) and empty == (502, "BAD_OUTPUT", 1),
            f"모양 틀림 → {shape}; 빈 content·length → {empty}")

    st, b, _ = h.post("/api/vision", {"image": IMG}, [(vision_out(), "stop")])
    c.check("items가 비면 실패가 아니라 '찾지 못함' — 200 ok와 빈 배열", st == 200 and b["ok"] and b["data"]["items"] == [],
            f"http={st}, data={b.get('data')}")

    bad_inputs = [
        ("image 없음", {}, "사진이 비어 있습니다."),
        ("data URI가 아님", {"image": "https://example.com/a.jpg"}, "이미지 형식이 아닙니다"),
        ("허용 안 한 형식(gif)", {"image": "data:image/gif;base64,R0lGOD"}, "이미지 형식이 아닙니다"),
        ("4MB를 넘는 image", {"image": "data:image/jpeg;base64," + "A" * (s.MAX_IMAGE_CHARS + 10)}, "4MB"),
    ]
    rows = []
    for label, payload, want in bad_inputs:
        st, b, _ = h.post("/api/vision", payload, [])
        rows.append((label, st, b.get("error", {}).get("message", ""), len(h.calls), want))
    st, b, _ = h.post("/api/vision", raw=b'{"image": "x"}', content_type="text/plain")
    rows.append(("text/plain", st, b.get("error", {}).get("message", ""), len(h.calls), "JSON으로"))
    st, b, _ = h.post("/api/vision", raw=b"{", outputs=())
    rows.append(("깨진 JSON 본문", st, b.get("error", {}).get("message", ""), len(h.calls), "JSON이 아닙니다"))
    h.calls.clear()
    st, b = h.post_declared_length("/api/vision", s.MAX_BODY_BYTES + 1)
    rows.append(("Content-Length가 상한 초과(본문 안 읽음)", st, b.get("error", {}).get("message", ""), len(h.calls), "4MB"))
    st, b, _ = h.post("/api/vision", {"image": IMG}, [], headers={"Host": "evil.example"})
    rows.append(("다른 Host(DNS 리바인딩)", st, b.get("error", {}).get("message", ""), len(h.calls), "허용되지 않은 주소"))
    c.check("입력 검증: 잘못된 요청은 모델을 부르지 않고 400 BAD_REQUEST와 구체적인 한국어 문구",
            all(r[1] == 400 and r[4] in r[2] and r[3] == 0 for r in rows),
            "; ".join(f"{r[0]} → {r[1]} {r[2]!r} 모델호출 {r[3]}" for r in rows))


# ── 3. /api/recipe ──────────────────────────────────────────────────────

def check_recipe(c: Checker, h: Harness) -> None:
    print("\n── 3. /api/recipe (PRD_step2 · UNIT_json_contract · UNIT_proxy)")
    ing = [{"name": "달걀", "quantity": "6개", "confidence": "high"}, {"name": "두부", "quantity": "미상"}, {"name": "달걀", "quantity": "2개"},
           {"name": "  ", "quantity": "1"}, "쓰레기"]
    st, b, _ = h.post("/api/recipe", {"ingredients": ing, "count": 3, "diet": "채식"}, [(recipe_out(recipe()), "stop")])
    call = h.calls[0] if h.calls else {}
    prompt = (call.get("messages") or [{}])[0].get("content", "")
    extra = call.get("extra") or {}
    c.check("모델 요청: 이름·수량만(중복·빈 이름·'미상' 정리, 다른 필드 무시), max_tokens 8000·temperature 0.3·effort=low·정수 seed",
            st == 200 and prompt.splitlines()[0] == "재료: 달걀 6개, 두부" and "요리 3개를 추천하라" in prompt and "채식" not in prompt
            and "confidence" not in prompt and call.get("max_tokens") == 8000 and call.get("temperature") == 0.3
            and extra.get("reasoning") == {"effort": "low"} and isinstance(extra.get("seed"), int) and b["meta"].get("seed") == extra.get("seed"),
            f"프롬프트 첫 줄={prompt.splitlines()[0] if prompt else None!r}, 파라미터={call.get('max_tokens')}/{call.get('temperature')}/{extra}, meta.seed={b.get('meta', {}).get('seed')}")

    st, b, _ = h.post("/api/recipe", {"ingredients": ing}, [("```json\n" + recipe_out(recipe()) + "\n```", "stop")])
    fence = (st, b["ok"], len(h.calls))
    st, b, _ = h.post("/api/recipe", {"ingredients": ing}, [("추천입니다", "stop"), (recipe_out(recipe()), "stop")])
    seeds = [x["extra"]["seed"] for x in h.calls]
    repair = (st, b["ok"], [len(x["messages"]) for x in h.calls], b.get("meta", {}).get("repaired"))
    c.check("코드펜스는 바로 파싱, 1회 실패는 이전 출력을 붙여 복구(복구 호출도 같은 seed)",
            fence == (200, True, 1) and repair == (200, True, [1, 3], True) and len(set(seeds)) == 1,
            f"펜스 → {fence}; 복구 → http={repair[0]} 메시지 수 {repair[2]} repaired={repair[3]} seed 동일={len(set(seeds)) == 1}")

    st, b, _ = h.post("/api/recipe", {"ingredients": ing}, [("아님", "stop"), ("또 아님", "stop")])
    fail = (st, b.get("error"), len(h.calls))
    st2, b2, _ = h.post("/api/recipe", {"ingredients": ing}, [(recipe_out(recipe()), "stop")])
    st3, b3, _ = h.post("/api/recipe", {"ingredients": ing}, [('{"recipes": []}', "stop"), ('{"recipes": []}', "stop")])
    st4, b4, _ = h.post("/api/recipe", {"ingredients": ing}, [(recipe_out(recipe(title="", steps=[])), "stop")])
    st5, b5, _ = h.post("/api/recipe", {"ingredients": ing}, [("", "length")])
    codes = [x.get("error", {}).get("code") for x in (b3, b4, b5)]
    c.check("2회 실패·빈 recipes·쓸 수 있는 레시피 0개·빈 content는 BAD_OUTPUT, 직후 요청은 정상",
            fail[0] == 502 and fail[1]["code"] == "BAD_OUTPUT" and fail[1]["retryable"] and fail[2] == 2 and b2["ok"] and codes == ["BAD_OUTPUT"] * 3,
            f"2회 실패 → http={fail[0]} {fail[1]} 호출 {fail[2]}회; 직후 ok={b2['ok']}; 빈 recipes/제목·조리순서 없음/빈 content → {codes}")

    outs = recipe_out(recipe(), recipe(title="둘", difficulty="매우쉬움", time_minutes="15분", servings=99, uses=["달걀", "달걀", " 두부 "]),
                      recipe(title="", steps=["x"]), recipe(title="넷", steps=[]), recipe(title="다섯"))
    st, b, _ = h.post("/api/recipe", {"ingredients": ing, "count": 3}, [(outs, "stop")])
    rs = b["data"]["recipes"]
    brief = [(r["title"], r["difficulty"], r["time_minutes"], r["servings"], r["uses"]) for r in rs]
    c.check("값 검증: 난이도 밖→보통, '15분'→15, 인분 범위 밖→null, uses 중복·공백 정리, 제목·조리순서 없는 것 제외, 개수 상한",
            brief == [("김치 두부 계란찜", "쉬움", 20, 2, ["달걀"]), ("둘", "보통", 15, None, ["달걀", "두부"]), ("다섯", "쉬움", 20, 2, ["달걀"])]
            and all(ISO_RE.match(r["generated_at"]) for r in rs),
            f"{brief}")

    ids = [r["id"] for r in rs]
    h.post("/api/recipe", {"ingredients": ing}, [(recipe_out(recipe()), "stop")])
    _, x1, _ = h.post("/api/recipe", {"ingredients": ing}, [(recipe_out(recipe()), "stop")])
    _, x2, _ = h.post("/api/recipe", {"ingredients": ing}, [(recipe_out(recipe()), "stop")])
    i1, i2 = x1["data"]["recipes"][0]["id"], x2["data"]["recipes"][0]["id"]
    c.check("id는 r-<14자리 시각>-<무작위 4자>-<순번>, 연달아 요청해도 겹치지 않는다",
            all(re.fullmatch(r"r-\d{14}-[a-z0-9]{4}-\d", i) for i in ids + [i1, i2]) and i1 != i2 and len({i[:-2] for i in ids}) == 1,
            f"한 번에 받은 id={ids}; 연속 두 요청 {i1} · {i2}")

    st, b, _ = h.post("/api/recipe", {"ingredients": ing, "count": 2}, [(recipe_out(recipe(), recipe(title="둘"), recipe(title="셋")), "stop")])
    c.check("count=2면 프롬프트도 '요리 2개'이고 모델이 더 줘도 2개로 자른다",
            len(b["data"]["recipes"]) == 2 and "요리 2개를 추천하라" in h.calls[0]["messages"][0]["content"], f"받은 레시피 {len(b['data']['recipes'])}개")

    rows = []
    for label, payload, want in [("재료 목록 없음", {}, "재료 목록이 없습니다."), ("빈 재료", {"ingredients": [], "count": 3}, "재료를 하나 이상"),
                                 ("이름이 전부 빈 값", {"ingredients": [{"name": "  "}]}, "재료를 하나 이상"),
                                 ("count=5", {"ingredients": ing, "count": 5}, "2 또는 3"), ("count=true", {"ingredients": ing, "count": True}, "2 또는 3")]:
        st, b, _ = h.post("/api/recipe", payload, [])
        rows.append((label, st, b.get("error", {}).get("message", ""), len(h.calls), want))
    c.check("입력 검증: 잘못된 요청은 모델을 부르지 않고 400 BAD_REQUEST",
            all(r[1] == 400 and r[4] in r[2] and r[3] == 0 for r in rows), "; ".join(f"{r[0]} → {r[1]} {r[2]!r}" for r in rows))


# ── 4. call_model ───────────────────────────────────────────────────────

OK_PAYLOAD = {"choices": [{"message": {"content": "{}", "reasoning": "긴 생각"}, "finish_reason": "stop"}],
              "usage": {"prompt_tokens": 5, "completion_tokens": 7}}


class FakeUpstream:
    """urllib.request.urlopen 자리에 들어가 정해진 순서대로 응답하거나 예외를 던진다."""

    def __init__(self, script):
        self.script = list(script)
        self.requests: list[dict] = []
        self.error_body_read = False

    def urlopen(self, req, timeout=None):
        self.requests.append({"timeout": timeout, "body": json.loads(req.data), "headers": {k.lower(): v for k, v in req.header_items()}})
        kind, *arg = self.script.pop(0)
        if kind == "ok":
            return io.BytesIO(json.dumps(arg[0]).encode())
        if kind == "http":
            upstream = self

            class ErrorBody(io.BytesIO):
                def read(self, *a):
                    upstream.error_body_read = True
                    return super().read(*a)
            raise urllib.error.HTTPError(req.full_url, arg[0], "error", {}, ErrorBody(b'{"error": "echo of request headers"}'))
        if kind == "timeout":
            raise TimeoutError("timed out")
        if kind == "urlerror-timeout":
            raise urllib.error.URLError(TimeoutError("timed out"))
        if kind == "refused":
            raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        if kind == "not-json":
            return io.BytesIO(b"<html>bad gateway</html>")
        raise AssertionError(kind)


def call(script, *, budget: float = 60.0, extra=None, missing_key: bool = False):
    """`(결과 또는 오류, 가짜 업스트림, sleep 기록)`"""
    up, sleeps = FakeUpstream(script), []
    orig = (urllib.request.urlopen, config.auth_header, time.sleep, s.log)

    def auth(**kw):
        if missing_key:
            raise config.MissingKeyError("키 없음(점검용)")
        return {"Authorization": "Bearer TEST-ONLY", "Content-Type": "application/json", **kw}

    # 서버의 "재시도: ..." 로그가 점검 결과 사이에 섞이지 않게 끈다.
    urllib.request.urlopen, config.auth_header, time.sleep, s.log = up.urlopen, auth, sleeps.append, (lambda line: None)
    try:
        r = s.call_model("fake/model", [{"role": "user", "content": "hi"}], max_tokens=100, temperature=0.3,
                         deadline=time.monotonic() + budget, extra=extra)
        return {"ok": r}, up, sleeps
    except s.ApiError as e:
        return {"code": e.code, "retryable": e.retryable, "message": e.message}, up, sleeps
    finally:
        urllib.request.urlopen, config.auth_header, time.sleep, s.log = orig


def check_call_model(c: Checker) -> None:
    print("\n── 4. call_model — 재시도·예산·오류 변환 (UNIT_model_call · UNIT_proxy)")
    r, up, _ = call([("ok", OK_PAYLOAD)], extra={"reasoning": {"effort": "low"}, "seed": 7})
    req = up.requests[0]
    c.check("정상: content·finish·토큰·시도 횟수만 돌려주고 reasoning은 버린다 / extra·X-Title이 요청에 실린다",
            r.get("ok") == {"content": "{}", "finish": "stop", "tokens_in": 5, "tokens_out": 7, "attempts": 1}
            and req["body"]["reasoning"] == {"effort": "low"} and req["body"]["seed"] == 7 and req["body"]["max_tokens"] == 100
            and req["headers"].get("x-title") == s.APP_TITLE and 59 < req["timeout"] <= 60,
            f"결과={r}, 요청 본문 키={sorted(req['body'])}, X-Title={req['headers'].get('x-title')!r}, urlopen timeout={req['timeout']:.2f}초(예산 60초)")

    rows = {}
    rows["429"] = call([("http", 429)])
    rows["500→200"] = call([("http", 500), ("ok", OK_PAYLOAD)])
    rows["503→503"] = call([("http", 503), ("http", 503)])
    rows["연결 거부→200"] = call([("refused",), ("ok", OK_PAYLOAD)])
    rows["HTML 본문→200"] = call([("not-json",), ("ok", OK_PAYLOAD)])
    rows["본문 error 502→200"] = call([("ok", {"error": {"code": 502}}), ("ok", OK_PAYLOAD)])
    rows["본문 error 429"] = call([("ok", {"error": {"code": 429}})])
    rows["타임아웃"] = call([("timeout",)])
    rows["URLError(타임아웃)"] = call([("urlerror-timeout",)])
    rows["401"] = call([("http", 401)])
    rows["403"] = call([("http", 403)])
    rows["400"] = call([("http", 400)])
    summary = {k: ((v[0].get("code") or f"ok·{v[0]['ok']['attempts']}회"), len(v[1].requests), v[2]) for k, v in rows.items()}
    show = "; ".join(f"{k} → {code} 요청 {n}회 sleep {sl}" for k, (code, n, sl) in summary.items())

    c.check("429는 자동 재시도하지 않는다(본문 안의 429도) → RATE_LIMIT(재시도 가능)",
            rows["429"][0] == {"code": "RATE_LIMIT", "retryable": True, "message": s.ERRORS["RATE_LIMIT"][2]} and len(rows["429"][1].requests) == 1
            and rows["본문 error 429"][0]["code"] == "RATE_LIMIT" and len(rows["본문 error 429"][1].requests) == 1, show)
    c.check("5xx·연결 실패·깨진 본문·본문 안의 5xx는 2초 쉬고 1회만 재시도, 두 번째도 실패면 UPSTREAM(재시도 가능)",
            all(summary[k][0] == "ok·2회" and summary[k][2] == [s.RETRY_WAIT_S] for k in ("500→200", "연결 거부→200", "HTML 본문→200", "본문 error 502→200"))
            and rows["503→503"][0]["code"] == "UPSTREAM" and rows["503→503"][0]["retryable"] and len(rows["503→503"][1].requests) == 2,
            "; ".join(f"{k} → {summary[k]}" for k in ("500→200", "연결 거부→200", "HTML 본문→200", "본문 error 502→200", "503→503")))
    c.check("타임아웃은 재시도하지 않는다(예산을 다 쓴 것) → TIMEOUT",
            all(rows[k][0]["code"] == "TIMEOUT" and len(rows[k][1].requests) == 1 and not rows[k][2] for k in ("타임아웃", "URLError(타임아웃)")),
            "; ".join(f"{k} → {summary[k]}" for k in ("타임아웃", "URLError(타임아웃)")))
    c.check("401·403은 NO_KEY(키 거부, 재시도 불가), 그 밖의 4xx는 UPSTREAM(재시도 불가) — 둘 다 재시도 없음",
            all(rows[k][0]["code"] == "NO_KEY" and not rows[k][0]["retryable"] and "거부" in rows[k][0]["message"] and len(rows[k][1].requests) == 1 for k in ("401", "403"))
            and rows["400"][0]["code"] == "UPSTREAM" and not rows["400"][0]["retryable"] and len(rows["400"][1].requests) == 1,
            f"401 → {rows['401'][0]}; 403 → {rows['403'][0]['code']}; 400 → {rows['400'][0]}")
    c.check("오류 응답의 본문은 읽지 않는다(요청 헤더가 섞여 나올 수 있다)",
            not any(v[1].error_body_read for v in rows.values()), f"본문을 읽은 경우={[k for k, v in rows.items() if v[1].error_body_read]}")

    tight, up_t, sl_t = call([("http", 500), ("ok", OK_PAYLOAD)], budget=s.MIN_ATTEMPT_S + s.RETRY_WAIT_S - 1)
    gone, up_g, _ = call([("ok", OK_PAYLOAD)], budget=-1)
    nokey, up_k, _ = call([("ok", OK_PAYLOAD)], missing_key=True)
    c.check("타임아웃은 요청 하나의 총 예산: 남은 예산이 모자라면 재시도하지 않고, 이미 끝났으면 부르지도 않는다",
            tight.get("code") == "UPSTREAM" and len(up_t.requests) == 1 and not sl_t and gone.get("code") == "TIMEOUT" and not up_g.requests,
            f"예산 {s.MIN_ATTEMPT_S + s.RETRY_WAIT_S - 1:.0f}초에서 500 → {tight.get('code')} 요청 {len(up_t.requests)}회 sleep {sl_t}; "
            f"예산 소진 → {gone.get('code')} 요청 {len(up_g.requests)}회")
    c.check("키를 못 읽으면(MissingKeyError) OpenRouter를 부르지 않고 NO_KEY",
            nokey.get("code") == "NO_KEY" and not nokey.get("retryable") and not up_k.requests, f"{nokey}, 요청 {len(up_k.requests)}회")


# ── 5. 보안 ─────────────────────────────────────────────────────────────

def check_security(c: Checker) -> None:
    print("\n── 5. 보안 — 나가는 문자열은 전부 가린다 (UNIT_proxy)")
    captured = io.StringIO()
    real_stderr = sys.stderr
    h = Harness(quiet=False)           # 로그를 끄지 않는다 — 실제 log()가 가리는지 본다
    try:
        sys.stderr = captured
        st1, _, t1 = h.post("/api/vision", {"image": IMG}, [(vision_out(item(f"{FAKE_KEY} 병", "기타")), "stop")])
        st2, _, t2 = h.post("/api/recipe", {"ingredients": [{"name": "달걀", "quantity": "1개"}]},
                            [(recipe_out(recipe(tips=f"키 {FAKE_KEY} 는 비밀")), "stop")])
        st3, _, t3 = h.post("/api/vision", {"image": IMG}, [(f"{FAKE_KEY} 가 섞인 JSON 아닌 출력", "stop"), (f"{FAKE_KEY} 또", "stop")])
    finally:
        sys.stderr = real_stderr
        h.close()
    logs = captured.getvalue()
    c.check("모델 출력에 키 모양 문자열이 섞여도 브라우저로 가는 응답에서는 가려진다",
            FAKE_KEY not in t1 + t2 and REDACTED_PREFIX in t1 and REDACTED_PREFIX in t2 and st1 == st2 == 200,
            f"vision 응답에 원문 {t1.count(FAKE_KEY)}건·가린 형태 {t1.count(REDACTED_PREFIX)}건, recipe 응답에 원문 {t2.count(FAKE_KEY)}건·가린 형태 {t2.count(REDACTED_PREFIX)}건")
    c.check("BAD_OUTPUT 때 서버 로그에 남기는 모델 출력 조각도 가려진다",
            st3 == 502 and FAKE_KEY not in logs and REDACTED_PREFIX in logs and FAKE_KEY not in t3,
            f"로그 {len(logs.splitlines())}줄 중 원문 {logs.count(FAKE_KEY)}건·가린 형태 {logs.count(REDACTED_PREFIX)}건, 응답 원문 {t3.count(FAKE_KEY)}건")


def main() -> int:
    c = Checker()
    check_pure(c)
    h = Harness()
    try:
        check_vision(c, h)
        check_recipe(c, h)
    finally:
        h.close()
    check_call_model(c)
    check_security(c)
    return c.summary("프록시 계약 점검", show_work=False)


if __name__ == "__main__":
    raise SystemExit(main())
