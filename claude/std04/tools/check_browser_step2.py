#!/usr/bin/env python3
"""2단계(재료 → 레시피) 브라우저 체크리스트. 개발 전용 — 앱은 로드하지 않는다.

    python3 tools/check_browser_step2.py            # 실제 모델 API 3회(추천 2회, 1단계 회귀 분석 1회)
    python3 tools/check_browser_step2.py --offline  # 가짜 응답으로 확인(API 0회). 모델 품질 항목은 건너뛴다

PRD_step2.md와 UNIT_json_contract·UNIT_ui_state의 검증 항목 중 화면에서 확인할 것을 돈다.
배지·정렬·BAD_OUTPUT 화면은 모델 출력과 상관없이 확인하려고 항상 정해진 응답을 끼워 넣는다.
프록시 쪽 "복구 1회 후 BAD_OUTPUT"은 이 스크립트가 아니라 가짜 모델로 server.py를 직접 부르는 점검에서 본다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser_cdp as bc  # noqa: E402

K = bc.KEY_INGREDIENTS
STAPLES = ("소금", "후추", "식용유")
SIX = [("달걀", "6개", "달걀"), ("우유", "1L", "유제품"), ("당근", "3개", "채소"), ("두부", "", "가공식품"), ("김치", "", "가공식품"), ("양파", "2개", "채소")]
R = bc.recipe

FAKE_FIRST = bc.recipes_body(
    R("r-fake-a-1", "달걀 당근 양파 볶음", minutes=15, uses=("달걀", "당근", "양파")),
    R("r-fake-a-2", "김치 두부 계란찜", minutes=25, uses=("달걀", "두부", "김치"), missing=("참기름",)),
    R("r-fake-a-3", "우유 당근 수프", minutes=20, uses=("우유", "당근"), missing=("버터", "밀가루")), elapsed_ms=6000)
FAKE_WITHOUT_KIMCHI = bc.recipes_body(
    R("r-fake-b-1", "두부 양파 볶음", minutes=15, uses=("두부", "양파")),
    R("r-fake-b-2", "우유 계란찜", minutes=20, uses=("우유", "달걀")),
    R("r-fake-b-3", "당근 달걀말이", minutes=10, uses=("당근", "달걀"), missing=("대파",)))
# 정렬·배지 확인용. 입력 재료가 달걀·우유·당근·두부·양파일 때 기대 순서는 C → B → D → A
ORDER = bc.recipes_body(
    R("r-t-1", "A 부족2 10분", minutes=10, uses=("달걀",), missing=("대파", "버섯")),
    R("r-t-2", "B 바로 30분", minutes=30, uses=("우유",), difficulty="보통"),
    R("r-t-3", "C 바로 15분", minutes=15, uses=("당근", "양파"), steps=("하나", "둘", "셋"), servings=1),
    R("r-t-4", "D 이름바꿈 5분", minutes=5, uses=("계란", "두부")), elapsed_ms=1234)
BAD = {"ok": False, "error": {"code": "BAD_OUTPUT", "message": "결과를 읽지 못했습니다. 다시 시도하세요.", "retryable": True}}
FAKE_EMPTY_VISION = {"ok": True, "meta": {"elapsed_ms": 300}, "data": {"items": [], "unsure": [], "analyzed_at": "2026-09-14T10:00:00+09:00"}}


def expected_order(recipes: list[dict], names: list[str]) -> list[str]:
    have = set(names)
    rows = []
    for r in recipes:
        missing = list(dict.fromkeys([u for u in r["uses"] if u not in have] + r["missing"]))
        minutes = r["time_minutes"] if isinstance(r["time_minutes"], int) else 10**9
        rows.append((len(missing), minutes, r["title"]))
    return [t for _, _, t in sorted(rows, key=lambda x: (x[0], x[1]))]


def main() -> int:
    ap = argparse.ArgumentParser(description="2단계 브라우저 체크리스트")
    ap.add_argument("--offline", action="store_true", help="모델 API를 부르지 않고 가짜 응답으로 확인한다")
    a = ap.parse_args()

    c = bc.Checker()
    assets = bc.make_assets()
    real = not a.offline
    tag = "" if real else " (가짜 응답)"
    server, base = bc.start_server()
    extra = []
    b = bc.Browser("step2")
    try:
        b.launch()
        print(f"브라우저: {b.version} · 서버: {base} · 모델 호출: {'실제' if real else '가짜 응답'}\n", flush=True)

        # ── 1단계 없이 손으로만 입력 ──
        b.navigate(base + "/")
        b.js(f"localStorage.removeItem({json.dumps(K)}); true")
        b.reload()
        before = (b.js("document.getElementById('recommend').disabled"), b.js("document.getElementById('recipe-hint').textContent"))
        for n, q, cat in SIX:
            bc.add_item(b, n, q, cat)
        names6 = [n for n, _, _ in SIX]
        after = (b.js("document.getElementById('recommend').disabled"), b.js("document.getElementById('recipe-hint').textContent"),
                 b.js("document.getElementById('summary').textContent"))

        # ── 재료 6개로 추천 ──
        if not real:
            b.fake("/api/recipe", FAKE_FIRST, delay=6000)      # 경과 초를 볼 수 있게 늦게 답한다
        b.js("window.__fetches = []; true")
        t0 = time.time()
        bc.click(b, "recommend", 3)
        dis, cancel = b.js("document.getElementById('recommend').disabled"), b.js("!document.getElementById('recipe-cancel').hidden")
        loading = b.js("document.getElementById('recipe-status').textContent")
        time.sleep(2); e1 = b.js("document.getElementById('recipe-elapsed').textContent")
        time.sleep(3); e2 = b.js("document.getElementById('recipe-elapsed').textContent")
        state = b.wait(bc.RECIPE_DONE, 45, 0.5)
        took = time.time() - t0
        n_req = b.fetch_count("/api/recipe")
        sent = b.js("(window.__fetches.find(f => f.url.includes('/api/recipe')) || {}).body")
        status = b.js("document.getElementById('recipe-status').textContent")
        err = b.js("!document.getElementById('recipe-error').hidden && document.getElementById('recipe-error-msg').textContent")
        dom1 = b.js(bc.CARDS)
        b.shot("step2_first.png")

        c.check(f"[step2] 재료를 손으로만 입력해도(1단계 건너뛰고) 동작한다{tag}",
                before[0] and not after[0] and state == "done" and len(dom1) > 0,
                f"빈 목록일 때 추천 버튼 disabled={before[0]} ({before[1]!r}) → 직접 추가 6개 후 disabled={after[0]} ({after[1]!r}), 목록 요약={after[2]!r}, 카드 {len(dom1)}장")
        c.check(f"[step2] 재료 6개를 넣으면 레시피 3개가 뜬다{tag}", state == "done" and len(dom1) == 3,
                f"{took:.1f}초, 상태={state} {status!r}, 오류={err!r}, 카드={[x['title'] for x in dom1]}")
        if real:
            c.check("[step2] 10초 안팎에 뜬다 (판정: 15초 이내 — 무료 모델이 붐비면 넘을 수 있다)", took <= 15, f"{took:.1f}초")
        else:
            c.skip("[step2] 10초 안팎에 뜬다", "--offline: 실제 모델 시간이 아니다")
        keys = sorted({k for i in (sent or {}).get("ingredients", []) for k in i})
        c.check("(참고) 요청에 이름·수량만 보낸다(IngredientList 통째로 안 보냄)", keys == ["name", "quantity"] and (sent or {}).get("count") == 3,
                f"보낸 본문 = {json.dumps(sent, ensure_ascii=False)}")
        c.check("[ui_state] 추천 중 버튼이 비활성화되고 두 번 눌리지 않는다", dis and n_req == 1,
                f"클릭 직후 recommend.disabled={dis}, 취소 보임={cancel}, 3번 클릭 → /api/recipe {n_req}회")
        c.check("[ui_state] 경과 초가 올라간다", bc.secs(e1) is not None and bc.secs(e2) is not None and bc.secs(e2) > bc.secs(e1),
                f"2초 뒤 {e1!r} → 5초 뒤 {e2!r}; 대기 문구={loading!r}")

        # ── 재료 하나 빼고 다시 ──
        b.js("""(() => { const li = Array.from(document.querySelectorAll('.item')).find(l => l.querySelector('.i-name').value === '김치');
            li.querySelector('.i-del').click(); return true; })()""")
        hint = b.js("document.getElementById('recipe-hint').textContent")
        if not real:
            b.fake("/api/recipe", FAKE_WITHOUT_KIMCHI)
        b.js("window.__fetches = []; true")
        t0 = time.time()
        bc.click(b, "recommend")
        state = b.wait(bc.RECIPE_DONE, 45, 0.5)
        took2 = time.time() - t0
        sent2 = b.js("(window.__fetches.find(f => f.url.includes('/api/recipe')) || {}).body") or {}
        dom2 = b.js(bc.CARDS)
        used = b.js("document.getElementById('recipe-used').textContent")
        t1, t2 = [x["title"] for x in dom1], [x["title"] for x in dom2]
        kimchi = [x["title"] for x in dom2 if "김치" in x["have"]]
        c.check(f"[step2] 재료를 하나 빼고 다시 누르면 결과가 달라진다{tag}",
                state == "done" and t1 != t2 and "김치" not in [i["name"] for i in sent2.get("ingredients", [])] and not kimchi,
                f"김치 삭제 후 힌트={hint!r}; {took2:.1f}초; 보낸 재료={[i['name'] for i in sent2.get('ingredients', [])]}; 전 {t1} → 후 {t2}; "
                f"김치를 가진 재료로 쓴 카드={kimchi}; {used!r}")

        # ── 실제 응답으로 모델 규칙 확인 ──
        if real:
            resps = [r for r in b.responses("/api/recipe") if r.get("ok")][-2:]
            pairs = list(zip(resps, [names6, [n for n in names6 if n != "김치"]], [dom1, dom2]))
            bad_uses = [(rc["title"], u) for r, names, _ in pairs for rc in r["data"]["recipes"] for u in rc["uses"] if u not in names]
            staples = [(rc["title"], m) for r, _, _ in pairs for rc in r["data"]["recipes"] for m in rc["missing"] if any(s in m for s in STAPLES)]
            order_ok = all(expected_order(r["data"]["recipes"], names) == [x["title"] for x in dom] for r, names, dom in pairs)
            badge_ok = all(x["badge"] == (not x["missing"]) for _, _, dom in pairs for x in dom)
            c.check("[step2] 소금·후추·식용유가 missing에 나오지 않는다", len(pairs) == 2 and not staples,
                    f"실제 응답 {len(pairs)}건의 missing={[[m for rc in r['data']['recipes'] for m in rc['missing']] for r, _, _ in pairs]}; 기본 양념={staples}")
            c.check("[step2] uses의 이름이 입력 재료 이름과 글자까지 같다", len(pairs) == 2 and not bad_uses, f"입력 밖 이름={bad_uses}")
            c.check("(참고) 실제 응답도 화면 정렬·배지 규칙대로 그려진다", len(pairs) == 2 and order_ok and badge_ok,
                    f"정렬 일치={order_ok}, 배지는 missing이 빈 카드에만={badge_ok}")
        else:
            for name in ("[step2] 소금·후추·식용유가 missing에 나오지 않는다", "[step2] uses의 이름이 입력 재료 이름과 글자까지 같다",
                         "(참고) 실제 응답도 화면 정렬·배지 규칙대로 그려진다"):
                c.skip(name, "--offline: 모델 출력 품질 항목이라 실제 응답이 있어야 한다")

        # ── 배지·정렬 (정해진 응답) ──
        b.fake("/api/recipe", ORDER, delay=50)
        bc.click(b, "recommend")
        b.wait(bc.RECIPE_DONE, 10)
        dom = b.js(bc.CARDS)
        order = [x["title"] for x in dom]
        d = next((x for x in dom if x["title"].startswith("D")), {})
        c3 = next((x for x in dom if x["title"].startswith("C")), {})
        c.check("[step2] missing이 빈 레시피에 '지금 바로 가능' 배지가 붙고 맨 위에 온다",
                order == ["C 바로 15분", "B 바로 30분", "D 이름바꿈 5분", "A 부족2 10분"] and [x["badge"] for x in dom] == [True, True, False, False],
                f"(A:부족2·10분 / B:0·30분 / C:0·15분 / D:uses에 '계란'·5분) → 화면 순서 {order}, 배지 {[x['badge'] for x in dom]}")
        c.check("[step2] uses 중 입력에 없는 이름은 missing으로 옮긴다 / 단계 번호는 화면이 붙인다",
                d.get("missing") == ["계란"] and d.get("have") == ["두부"] and c3.get("steps") == 3 and c3.get("meta") == "15분 · 쉬움 · 1인분",
                f"D: 가진 재료 {d.get('have')}, 부족한 재료 {d.get('missing')}; C: 단계 <li> {c3.get('steps')}개, 메타 {c3.get('meta')!r}")
        b.shot("step2_order.png")

        # ── needs_review 항목은 보내지 않는다 ──
        b.set_list(bc.BROKEN_LIST)
        b.reload()
        b.fake("/api/recipe", ORDER, delay=50)
        b.js("window.__fetches = []; true")
        hint = b.js("document.getElementById('recipe-hint').textContent")
        bc.click(b, "recommend")
        b.wait(bc.RECIPE_DONE, 10)
        sent5 = b.js("(window.__fetches.find(f => f.url.includes('/api/recipe')) || {}).body") or {}
        b.js("""(() => { const li = Array.from(document.querySelectorAll('.item')).find(l => l.querySelector('.i-name').value.includes('modification'));
            const n = li.querySelector('.i-name'); n.value = '달걀'; n.dispatchEvent(new Event('change', { bubbles: true })); return true; })()""")
        hint2 = b.js("document.getElementById('recipe-hint').textContent")
        names5 = [i["name"] for i in sent5.get("ingredients", [])]
        c.check("[json_contract] needs_review 항목은 고치기 전까지 다음 단계로 넘기지 않는다",
                "달modification달걀" not in names5 and len(names5) == 2 and "확인 필요 1개" in hint and "재료 3개로" in hint2 and "확인 필요" not in hint2,
                f"고치기 전 힌트={hint!r}, 보낸 재료={names5}; 고친 뒤 힌트={hint2!r}")

        # ── BAD_OUTPUT 화면 → 재시도 ──
        items = b.js("document.querySelectorAll('.item').length")
        n0 = len(b.cdp.events)
        b.fake("/api/recipe", BAD, status=502, delay=50)
        bc.click(b, "recommend")
        msg = b.wait("!document.getElementById('recipe-error').hidden && document.getElementById('recipe-error-msg').textContent", 10)
        retry = b.js("!document.getElementById('recipe-retry').hidden")
        b.fake("/api/recipe", ORDER, delay=50)
        bc.click(b, "recipe-retry")
        b.wait(bc.RECIPE_DONE, 10)
        after6 = (len(b.js(bc.CARDS)), b.js("document.getElementById('recipe-error').hidden"), b.js("document.querySelectorAll('.item').length"))
        exc = b.exceptions_since(n0)
        c.check("[step2·json_contract] BAD_OUTPUT이 오면 화면이 알리고 앱이 죽지 않는다(재시도하면 다시 된다)",
                msg == BAD["error"]["message"] and retry and after6 == (4, True, items) and not exc,
                f"오류={msg!r}, 재시도 버튼={retry}; 재시도 뒤 카드 {after6[0]}장·오류 숨김={after6[1]}, 재료 {items}→{after6[2]}개, JS 예외={exc}")

        # ── 480px (카드가 있는 상태) ──
        b.viewport(480)
        time.sleep(0.5)
        lay = bc.overflow(b)
        p = b.shot("step2_480.png")
        c.check("[ui_state] 480px에서 레이아웃이 유지된다(레시피 카드 포함)", lay["scroll"] <= lay["inner"] and not lay["over"],
                f"innerWidth={lay['inner']}, scrollWidth={lay['scroll']}, 넘친 요소={lay['over']}, 스크린샷={p.name}")
        b.viewport(1280)

        # ── 1단계 회귀 (두 단계가 대기 함수를 함께 쓴다) ──
        b.navigate(base + "/")
        b.set_file(assets["pdf"])
        rej = b.wait("!document.getElementById('error').hidden && document.getElementById('error-msg').textContent", 5)
        b.set_file(assets["landscape"])
        b.wait("!document.getElementById('analyze').disabled", 10)
        if not real:
            b.fake("/api/vision", FAKE_EMPTY_VISION, delay=1500)
        b.js("window.__fetches = []; true")
        bc.click(b, "analyze", 2)
        time.sleep(1.2)
        el = b.js("document.getElementById('elapsed').textContent")
        st = b.wait(bc.STATUS_DONE, 90, 0.5)
        msg = b.js("document.getElementById('status').textContent")
        nv = b.fetch_count("/api/vision")
        c.check(f"(회귀) 1단계 분석이 공용 대기 함수에서 그대로 동작한다{tag}", bool(rej) and st == "done" and "찾지 못했습니다" in msg and nv == 1,
                f"PDF 거부={rej!r}; 풍경 분석: 상태={st}, 문구={msg!r}, 두 번 클릭→요청 {nv}회, 1.2초 시점 경과={el!r}")

        # ── NO_KEY (추천 패널) ──
        (bc.WORK / "fakehome").mkdir(exist_ok=True)
        srv, url = bc.start_server(env_extra={"HOME": str(bc.WORK / "fakehome")}, unset_key=True)
        extra.append(srv)
        b.navigate(url + "/")
        bc.add_item(b, "달걀", "2개", "달걀")
        bc.click(b, "recommend")
        msg = b.wait("!document.getElementById('recipe-error').hidden && document.getElementById('recipe-error-msg').textContent", 15)
        hidden = b.js("document.getElementById('recipe-retry').hidden")
        c.check("[ui_state] NO_KEY 오류에는 재시도 버튼이 없다(추천 패널)", bool(msg) and "키" in msg and hidden, f"오류={msg!r}, 재시도 버튼 숨김={hidden}")

        # ── 프록시를 끄고 추천 ──
        srv, url = bc.start_server()
        extra.append(srv)
        b.navigate(url + "/")
        bc.add_item(b, "달걀", "2개", "달걀")
        b.wait("!document.getElementById('recommend').disabled", 5)
        bc.stop(srv)
        bc.click(b, "recommend")
        msg = b.wait("!document.getElementById('recipe-error').hidden && document.getElementById('recipe-error-msg').textContent", 15)
        retry = b.js("!document.getElementById('recipe-retry').hidden")
        again = b.js("!document.getElementById('recommend').disabled")
        c.check("[ui_state] 프록시를 끈 채 추천하면 한국어 오류 한 줄과 재시도 버튼이 뜬다",
                bool(msg) and "연결할 수 없습니다" in msg and retry and again, f"오류={msg!r}, 재시도 버튼 보임={retry}, 추천 버튼 다시 사용 가능={again}")

        # ── 보안 ──
        ok, evidence, console = b.security()
        c.check("[보안] 응답·콘솔·네트워크 탭 어디에도 sk-or-v1-이 없다", ok, evidence)
        print(f"\n참고 — 콘솔 메시지(중복 제거): {console or '없음'}")
    finally:
        for p in extra:
            bc.stop(p)
        bc.stop(server)
        b.close()
    return c.summary("2단계 브라우저 점검")


if __name__ == "__main__":
    raise SystemExit(main())
