#!/usr/bin/env python3
"""3단계(프로필 · 레시피 보관함) 브라우저 체크리스트. 개발 전용 — 앱은 로드하지 않는다.

    python3 tools/check_browser_step3.py

모델 API는 부르지 않는다 — 레시피 카드는 /api/recipe 응답을 가로채 정해진 값으로 만든다.
PRD_step3.md와 UNIT_storage·UNIT_ui_state의 검증 항목 중 화면에서 확인할 것을 돈다.
용량 초과는 흉내 내지 않고 localStorage를 **1바이트 쓰기도 실패할 때까지** 실제로 채워서 본다
(1KB 단위로만 채우면 틈이 남아 저장이 성공해 버린다 — 실제로 한 번 그렇게 잘못 판정했다).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser_cdp as bc  # noqa: E402

PROFILES, ACTIVE, SAVED = "fridge.v1.profiles", "fridge.v1.activeProfile", "fridge.v1.saved."
BLOCK_STORAGE = """Object.defineProperty(window, 'localStorage', { configurable: true,
  get() { throw new DOMException('blocked', 'SecurityError'); } });"""
FILL_TO_THE_BYTE = """(() => {
  let size = 1 << 20, n = 0, ok = 0, last = null;
  while (size >= 1) {
    try { localStorage.setItem('fridge.v1.__fill_' + (n++), 'x'.repeat(size)); ok++; }
    catch (e) { last = e.name; size = size >> 1; }
  }
  let oneByte = 'ok'; try { localStorage.setItem('fridge.v1.__fill_probe', 'x'); } catch (e) { oneByte = e.name; }
  return { writes: ok, lastError: last, oneByteWrite: oneByte }; })()"""
UNFILL = "Object.keys(localStorage).filter(k => k.startsWith('fridge.v1.__fill_')).forEach(k => localStorage.removeItem(k)); true"


def ids(arr):
    return [x["recipe"]["id"] for x in arr] if isinstance(arr, list) else arr


def show_recipes(b, prefix: str, n: int = 3) -> None:
    body = bc.recipes_body(*[bc.recipe(f"r-{prefix}-{i}", f"{prefix.upper()} 요리 {i}", minutes=10 + i) for i in range(1, n + 1)])
    b.fake("/api/recipe", body, delay=50)
    bc.click(b, "recommend")
    b.wait(f"document.querySelectorAll('#recipes .recipe .save-btn').length === {n}", 10)


def buttons(b) -> dict:
    return b.js("Object.fromEntries(Array.from(document.querySelectorAll('#recipes .recipe')).map(c => [c.dataset.id, (c.querySelector('.save-btn') || {}).textContent]))")


def click_save(b, rid: str) -> None:
    b.js(f"document.querySelector('#recipes .recipe[data-id=\"{rid}\"] .save-btn').click(); true")


def library(b) -> list:
    return b.js("Array.from(document.querySelectorAll('#library-list details')).map(d => d.dataset.id)")


def bar(b) -> dict:
    return b.js("({ current: document.getElementById('profile-current').textContent, count: document.getElementById('library-count').textContent })")


def open_menu(b) -> None:
    b.js("if (document.getElementById('profile-menu').hidden) document.getElementById('profile-toggle').click(); true")


def create_profile(b, name: str) -> None:
    open_menu(b)
    b.js(f"(() => {{ const f = document.getElementById('new-profile-form'); f.elements.name.value = {json.dumps(name)}; f.requestSubmit(); return true; }})()")


def switch_to(b, name: str) -> None:
    open_menu(b)
    b.js(f"Array.from(document.querySelectorAll('#profile-list .profile-item')).find(x => x.textContent.includes({json.dumps(name)})).click(); true")


def wait_profile(b, suffix: str) -> None:
    b.wait(f"document.getElementById('profile-current').textContent.endsWith({json.dumps(suffix)})", 5)


def main() -> int:
    c = bc.Checker()
    server, base = bc.start_server()
    b = bc.Browser("step3")
    try:
        b.launch()
        print(f"브라우저: {b.version} · 서버: {base} · 모델 호출: 없음\n", flush=True)
        ready = "document.getElementById('profile-current').textContent !== '프로필'"

        # ── 첫 실행 ──
        b.navigate(base + "/")
        b.wait(ready, 5)
        top, profs, act = bar(b), b.ls(PROFILES), b.ls(ACTIVE)
        hidden = b.js("document.getElementById('store-error').hidden")
        c.check("[step3] 첫 실행에 '기본' 프로필이 자동으로 생기고 바로 쓸 수 있다",
                top["current"].endswith("기본") and isinstance(profs, list) and len(profs) == 1 and profs[0]["name"] == "기본" and act == profs[0]["id"] and hidden,
                f"상단={top}, {PROFILES}={profs}, {ACTIVE}={act!r}, 저장 오류 안내 숨김={hidden}")

        # ── 프로필 두 개, 섞이지 않음 / 전환해도 재료 목록 그대로 ──
        for n, q, cat in [("달걀", "6개", "달걀"), ("두부", "", "가공식품"), ("양파", "2개", "채소")]:
            bc.add_item(b, n, q, cat)
        show_recipes(b, "a")
        click_save(b, "r-a-1")
        b.wait("document.getElementById('library-count').textContent === '1'", 5)
        btn_default = buttons(b)
        create_profile(b, "엄마")
        wait_profile(b, "엄마")
        btn_mom = buttons(b)
        click_save(b, "r-a-2")
        b.wait("document.getElementById('library-count').textContent === '1'", 5)
        lib_mom = library(b)
        b.js("window.__marker = 'same-page'; true")
        items_before = b.js("Array.from(document.querySelectorAll('.item .i-name')).map(i => i.value)")
        cards_before = b.js("document.querySelectorAll('#recipes .recipe').length")
        switch_to(b, "기본")
        wait_profile(b, "기본")
        lib_default, btn_back = library(b), buttons(b)
        items_after = b.js("Array.from(document.querySelectorAll('.item .i-name')).map(i => i.value)")
        cards_after = b.js("document.querySelectorAll('#recipes .recipe').length")
        marker = b.js("window.__marker")
        s1, s2 = b.ls(SAVED + "p-1"), b.ls(SAVED + "p-2")
        c.check("[step3] 프로필을 2개 만들고 각각 다른 레시피를 저장하면 서로 섞이지 않는다",
                ids(s1) == ["r-a-1"] and ids(s2) == ["r-a-2"] and lib_mom == ["r-a-2"] and lib_default == ["r-a-1"]
                and btn_mom["r-a-1"] == "저장" and btn_back == {"r-a-1": "저장됨", "r-a-2": "저장", "r-a-3": "저장"},
                f"saved.p-1={ids(s1)}, saved.p-2={ids(s2)}; 엄마 보관함={lib_mom}, 기본 보관함={lib_default}; "
                f"버튼 — 기본 저장 직후 {btn_default}, 엄마로 전환 직후 {btn_mom}, 기본으로 돌아온 뒤 {btn_back}")
        c.check("[step3] 프로필을 전환해도 화면의 재료 목록은 그대로다(페이지를 새로 그리지 않는다)",
                items_before == items_after and cards_before == cards_after == 3 and marker == "same-page",
                f"재료 {items_before} → {items_after}, 레시피 카드 {cards_before}→{cards_after}장, 페이지 표식 유지={marker == 'same-page'}")

        # ── 같은 레시피 두 번 저장 불가 ──
        force = "(() => { const x = document.querySelector('#recipes .recipe[data-id=\"r-a-1\"] .save-btn'); x.disabled = false; x.click(); x.click(); return true; })()"
        disabled = b.js("document.querySelector('#recipes .recipe[data-id=\"r-a-1\"] .save-btn').disabled")
        b.js(force)
        time.sleep(0.3)
        show_recipes(b, "a")          # 같은 id의 카드를 다시 받아도
        again = buttons(b)
        b.js(force)
        time.sleep(0.3)
        s1 = b.ls(SAVED + "p-1")
        c.check("[step3] 같은 레시피를 두 번 저장할 수 없다", disabled and ids(s1) == ["r-a-1"] and again["r-a-1"] == "저장됨" and bar(b)["count"] == "1",
                f"저장됨 버튼 disabled={disabled}; disabled를 강제로 풀고 클릭·같은 id 카드를 다시 받아 또 클릭 → saved.p-1={ids(s1)}, 버튼={again['r-a-1']!r}")

        # ── 프로필 삭제: 확인창 개수, saved 키 삭제 ──
        switch_to(b, "엄마")
        wait_profile(b, "엄마")
        click_save(b, "r-a-3")
        b.wait("document.getElementById('library-count').textContent === '2'", 5)
        open_menu(b)
        delete = "document.getElementById('profile-delete').click();"
        b.click_with_dialog(delete, accept=False)
        after_cancel = (len(b.ls(PROFILES)), b.raw(SAVED + "p-2") is not None)
        msg = b.click_with_dialog(delete, accept=True)
        wait_profile(b, "기본")
        profs = b.ls(PROFILES)
        c.check("[step3·storage] 프로필 삭제 시 레시피 개수가 확인창에 나오고, 삭제 후 saved.* 키가 사라진다",
                msg and "2개" in msg and after_cancel == (2, True) and len(profs) == 1 and b.raw(SAVED + "p-2") is None and b.ls(ACTIVE) == "p-1",
                f"확인창={msg!r}; [취소] 후 프로필 {after_cancel[0]}개·saved.p-2 남음={after_cancel[1]}; [확인] 후 프로필={[p['name'] for p in profs]}, "
                f"saved.p-2={b.raw(SAVED + 'p-2')!r}, 지금 프로필={b.ls(ACTIVE)!r}")

        # ── 마지막 프로필은 삭제되지 않는다 ──
        open_menu(b)
        dis = b.js("document.getElementById('profile-delete').disabled")
        note = b.js("document.getElementById('profile-delete-note').textContent")
        msg = b.click_with_dialog("const x = document.getElementById('profile-delete'); x.disabled = false; x.click();", accept=True, timeout=1.5)
        c.check("[step3] 마지막 프로필은 삭제되지 않는다", dis and msg is None and len(b.ls(PROFILES)) == 1,
                f"삭제 버튼 disabled={dis}, 안내={note!r}; disabled를 강제로 풀고 눌러도 확인창={msg!r}, 프로필 {len(b.ls(PROFILES))}개")

        # ── F5 후 유지 (이름 수정 포함) ──
        create_profile(b, "아빠")
        wait_profile(b, "아빠")
        show_recipes(b, "b", 2)
        click_save(b, "r-b-1")
        click_save(b, "r-b-2")
        b.wait("document.getElementById('library-count').textContent === '2'", 5)
        open_menu(b)
        b.js("(() => { const f = document.getElementById('rename-form'); f.elements.name.value = '우리 아빠'; f.requestSubmit(); return true; })()")
        b.reload()
        b.wait(ready, 5)
        top, lib = bar(b), library(b)
        names = [p["name"] for p in b.ls(PROFILES)]
        c.check("[step3·storage] F5 후에도 프로필과 보관함이 유지된다",
                top["current"].endswith("우리 아빠") and top["count"] == "2" and set(lib) == {"r-b-1", "r-b-2"} and names == ["기본", "우리 아빠"],
                f"F5 후 상단={top}, 보관함={lib}, 프로필={names} (이름 수정 '아빠'→'우리 아빠' 포함)")

        # ── 보관함에서 꺼내 보기·지우기 (완료 기준) ──
        b.js("document.querySelector('#library-list details[data-id=\"r-b-2\"] summary').click(); true")
        opened = b.js("(() => { const d = document.querySelector('#library-list details[data-id=\"r-b-2\"]'); return { open: d.open, steps: d.querySelectorAll('.recipe-steps li').length }; })()")
        b.js("document.querySelector('#library-list details[data-id=\"r-b-1\"]').parentElement.querySelector('.saved-del').click(); true")
        lib, st = library(b), ids(b.ls(SAVED + "p-2"))
        still_open = b.js("document.querySelector('#library-list details[data-id=\"r-b-2\"]').open")
        c.check("(완료 기준) 보관함에서 다시 꺼내 보고, 지울 수 있다", opened["open"] and opened["steps"] == 2 and lib == st == ["r-b-2"],
                f"펼침={opened}; r-b-1 삭제 후 화면={lib}, 저장소={st}, 펼쳐 둔 항목 유지={still_open}")

        # ── 저장 공간이 찼을 때 (실제로 1바이트까지 채운다) ──
        show_recipes(b, "c", 1)
        key = SAVED + b.ls(ACTIVE)
        before = ids(b.ls(key))
        fill = b.js(FILL_TO_THE_BYTE)
        b.js("document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0); true")
        click_save(b, "r-c-1")
        time.sleep(0.5)
        q = {"notice": b.js("!document.getElementById('store-error').hidden && document.getElementById('store-error').textContent"),
             "button": buttons(b).get("r-c-1"), "stored": ids(b.ls(key)), "focus": b.js("document.activeElement && document.activeElement.id")}
        b.shot("step3_quota.png")
        b.js(UNFILL)
        click_save(b, "r-c-1")
        time.sleep(0.4)
        q2 = {"hidden": b.js("document.getElementById('store-error').hidden"), "button": buttons(b).get("r-c-1"), "stored": ids(b.ls(key))}
        c.check("[step3·storage] 저장 공간이 찼을 때 조용히 실패하지 않고 사용자에게 알린다",
                fill["oneByteWrite"] == "QuotaExceededError" and q["notice"] and "저장 공간이 찼습니다" in q["notice"] and q["button"] == "저장"
                and q["stored"] == before and q["focus"] == "library" and q2["hidden"] and q2["button"] == "저장됨" and "r-c-1" in q2["stored"],
                f"채움 {fill}; 저장 시도 → 안내={q['notice']!r}, 버튼={q['button']!r}, 저장소 {before}→{q['stored']}, 포커스={q['focus']!r}; "
                f"채운 것을 지우고 다시 저장 → 안내 숨김={q2['hidden']}, 버튼={q2['button']!r}, 저장소={q2['stored']}")

        # ── localStorage를 손으로 깨뜨림 ──
        n0 = len(b.cdp.events)
        b.js(f"localStorage.setItem('{PROFILES}', '{{'); localStorage.setItem('{ACTIVE}', '{{'); localStorage.setItem('{SAVED}p-1', '{{'); true")
        b.reload()
        b.wait(ready, 5)
        top, profs, lib, exc = bar(b), b.ls(PROFILES), library(b), b.exceptions_since(n0)
        c.check("[step3·storage] localStorage를 손으로 깨뜨려도 앱이 죽지 않는다",
                not exc and top["current"].endswith("기본") and isinstance(profs, list) and lib == [],
                f"profiles·activeProfile·saved.p-1을 '{{'로 깬 뒤 F5 → 상단={top}, 복구된 profiles={profs}, 보관함={lib}, JS 예외={exc}")

        # ── 저장이 막힌 환경 ──
        sid = b.cdp.call("Page.addScriptToEvaluateOnNewDocument", source=BLOCK_STORAGE)["identifier"]
        n0 = len(b.cdp.events)
        b.reload()
        b.wait(ready, 5)
        top = bar(b)
        notice = b.js("!document.getElementById('store-error').hidden && document.getElementById('store-error').textContent")
        bc.add_item(b, "달걀", "1개", "달걀")
        show_recipes(b, "d", 1)
        click_save(b, "r-d-1")
        time.sleep(0.3)
        btn = buttons(b).get("r-d-1")
        create_profile(b, "차단테스트")
        created = bar(b)["current"]
        exc = b.exceptions_since(n0)
        c.check("[storage] 저장이 막힌 환경에서도 화면이 뜬다(저장만 안 될 뿐, 그 사실을 알린다)",
                top["current"].endswith("기본") and notice and "막고 있습니다" in notice and btn == "저장" and created.endswith("차단테스트") and not exc,
                f"상단={top}, 안내={notice!r}, 레시피 저장 시도 후 버튼={btn!r}, 프로필 만들기(메모리)→{created!r}, JS 예외={exc} "
                f"(실제 시크릿 창이 아니라 localStorage 접근이 던지게 만든 모의 환경)")
        b.cdp.call("Page.removeScriptToEvaluateOnNewDocument", identifier=sid)

        # ── 480px ──
        b.reload()
        create_profile(b, "모바일")
        bc.add_item(b, "달걀", "1개", "달걀")
        show_recipes(b, "e", 2)
        click_save(b, "r-e-1")
        b.viewport(480)
        open_menu(b)
        b.js("document.querySelector('#library-list details summary').click(); true")
        time.sleep(0.5)
        lay = bc.overflow(b, "body *")
        p = b.shot("step3_480.png")
        b.viewport(1280)
        c.check("[ui_state] 480px에서 레이아웃이 유지된다(프로필 메뉴·저장 버튼·보관함 포함)", lay["scroll"] <= lay["inner"] and not lay["over"],
                f"innerWidth={lay['inner']}, scrollWidth={lay['scroll']}, 넘친 요소={lay['over']}, 스크린샷={p.name}")

        # ── library.js 없이도 1·2단계가 돈다 ──
        b.cdp.call("Network.setBlockedURLs", urls=["*library.js"])
        n0 = len(b.cdp.events)
        b.reload()
        bc.add_item(b, "양파", "1개", "채소")
        b.fake("/api/recipe", bc.recipes_body(bc.recipe("r-f-1", "F 요리 1"), bc.recipe("r-f-2", "F 요리 2")), delay=50)
        bc.click(b, "recommend")
        b.wait("document.querySelectorAll('#recipes .recipe').length === 2", 10)
        no_lib = {"cards": b.js("document.querySelectorAll('#recipes .recipe').length"), "save": b.js("document.querySelectorAll('.save-btn').length"),
                  "bar": b.js("document.getElementById('profile-current').textContent"), "items": b.js("document.querySelectorAll('.item').length")}
        exc = b.exceptions_since(n0)
        b.cdp.call("Network.setBlockedURLs", urls=[])
        c.check("(참고) 3단계(library.js)를 빼도 1·2단계는 그대로 돈다", no_lib["cards"] == 2 and no_lib["save"] == 0 and no_lib["items"] > 0 and not exc,
                f"library.js 차단 후: 재료 {no_lib['items']}개, 카드 {no_lib['cards']}장, 저장 버튼 {no_lib['save']}개, 상단 표시={no_lib['bar']!r}, JS 예외={exc}")

        # ── 브라우저 재시작 후 유지 ──
        b.reload()
        b.wait(ready, 5)
        snapshot = ("({ profiles: localStorage.getItem('fridge.v1.profiles'), active: localStorage.getItem('fridge.v1.activeProfile'),"
                    " saved: Object.fromEntries(Object.keys(localStorage).filter(k => k.startsWith('fridge.v1.saved.')).map(k => [k, localStorage.getItem(k)])) })")
        before = (b.js(snapshot), bar(b), library(b))
        time.sleep(1.0)
        b.close(graceful=True)
        b.launch(fresh=False)
        b.navigate(base + "/")
        b.wait(ready, 5)
        after = (b.js(snapshot), bar(b), library(b))
        c.check("[step3] 브라우저를 완전히 껐다 켠 뒤에도 프로필과 보관함이 유지된다", before == after and after[2],
                f"같은 사용자 폴더로 Chromium 재시작: 전 {before[1]}·보관함 {before[2]} → 후 {after[1]}·보관함 {after[2]}, 저장소 원문 동일={before[0] == after[0]}")

        # ── 보안 · 모델 호출 없음 ──
        ok, evidence, console = b.security()
        api = sorted({e["params"]["request"]["url"].split("/", 3)[-1] for e in b.all_events()
                      if e["method"] == "Network.requestWillBeSent" and "/api/" in e["params"]["request"]["url"]})
        c.check("[보안] 응답·콘솔·네트워크 어디에도 sk-or-v1-이 없다 / 3단계는 모델 API를 부르지 않는다",
                ok and not any(x in api for x in ("api/recipe", "api/vision")), f"{evidence}; 실제로 나간 /api 요청={api}")
        print(f"\n참고 — 콘솔 메시지(중복 제거): {console or '없음'}")
    finally:
        bc.stop(server)
        b.close()
    return c.summary("3단계 브라우저 점검")


if __name__ == "__main__":
    raise SystemExit(main())
