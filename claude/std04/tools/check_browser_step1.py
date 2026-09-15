#!/usr/bin/env python3
"""1단계(사진 → 재료 목록) 브라우저 체크리스트. 개발 전용 — 앱은 로드하지 않는다.

    python3 tools/check_browser_step1.py              # 실제 모델 API 2회(냉장고 사진, 풍경 그림)
    python3 tools/check_browser_step1.py --offline    # 모델 호출을 가짜 응답으로 바꾼다(API 0회)
    python3 tools/check_browser_step1.py --photo 경로  # 분석할 냉장고 사진(기본: sample_fridge.jpg, 없으면 가짜 응답)

PRD_step1.md와 UNIT_image_input·UNIT_storage·UNIT_ui_state의 검증 항목 중 화면에서 확인할 것을 돈다.
서버는 빈 포트에 직접 띄우고, NO_KEY·프록시 종료 확인용 서버도 따로 띄웠다가 끈다.
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

FAKE_VISION = {"ok": True, "meta": {"elapsed_ms": 8000}, "data": {
    "items": [
        {"name": "달걀", "category": "달걀", "quantity": "10개", "confidence": "high", "needs_review": False, "source": "vision"},
        {"name": "당근", "category": "채소", "quantity": "3개", "confidence": "high", "needs_review": False, "source": "vision"},
        {"name": "마늘", "category": "채소", "quantity": "미상", "confidence": "high", "needs_review": False, "source": "vision"},
        {"name": "우유", "category": "유제품", "quantity": "1L", "confidence": "medium", "needs_review": False, "source": "vision"},
        {"name": "두부", "category": "가공식품", "quantity": "2개", "confidence": "low", "needs_review": False, "source": "vision"},
    ],
    "unsure": ["흰 병은 우유인지 주스인지 모르겠음"], "analyzed_at": "2026-09-14T10:00:00+09:00"}}
FAKE_EMPTY = {"ok": True, "meta": {"elapsed_ms": 300}, "data": {"items": [], "unsure": [], "analyzed_at": "2026-09-14T10:00:00+09:00"}}

PIXELS = """(async () => {
  const img = document.getElementById('preview-img'); await img.decode();
  const c = document.createElement('canvas'); c.width = img.naturalWidth; c.height = img.naturalHeight;
  const x = c.getContext('2d', { willReadFrequently: true }); x.drawImage(img, 0, 0);
  const at = (yy) => Array.from(x.getImageData(c.width >> 1, yy, 1, 1).data.slice(0, 3));
  return { w: img.naturalWidth, h: img.naturalHeight, top: at(10), bottom: at(c.height - 10) }; })()"""

PASTE = """(async () => {
  let seen = null;
  document.addEventListener('paste', e => { seen = !!(e.clipboardData && e.clipboardData.items.length); }, { once: true, capture: true });
  const c = document.createElement('canvas'); c.width = 320; c.height = 200;
  const x = c.getContext('2d'); x.fillStyle = '#08f'; x.fillRect(0, 0, 320, 200);
  const blob = await new Promise(r => c.toBlob(r, 'image/png'));
  const dt = new DataTransfer(); dt.items.add(new File([blob], 'screenshot.png', { type: 'image/png' }));
  document.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
  return seen; })()"""

DROP = """(async () => {
  const c = document.createElement('canvas'); c.width = 240; c.height = 160; c.getContext('2d').fillRect(0, 0, 240, 160);
  const blob = await new Promise(r => c.toBlob(r, 'image/png'));
  const dt = new DataTransfer(); dt.items.add(new File([blob], 'drop.png', { type: 'image/png' }));
  document.body.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
  return true; })()"""

BLOCK_STORAGE = """Object.defineProperty(window, 'localStorage', { configurable: true,
  get() { throw new DOMException('blocked', 'SecurityError'); } });"""


def main() -> int:
    ap = argparse.ArgumentParser(description="1단계 브라우저 체크리스트")
    ap.add_argument("--offline", action="store_true", help="모델 API를 부르지 않고 가짜 응답으로 확인한다")
    ap.add_argument("--photo", type=Path, default=bc.ROOT / "sample_fridge.jpg", help="분석할 냉장고 사진")
    a = ap.parse_args()

    c = bc.Checker()
    assets = bc.make_assets()
    real = not a.offline and a.photo.is_file()
    tag = "" if real else " (가짜 응답)"
    server, base = bc.start_server()
    extra = []
    b = bc.Browser("step1")
    try:
        b.launch()
        print(f"브라우저: {b.version} · 서버: {base} · 모델 호출: {'실제' if real else '가짜 응답'}\n", flush=True)
        if not a.offline and not a.photo.is_file():
            print(f"※ {a.photo} 가 없어 분석 항목은 가짜 응답으로 확인합니다.\n")

        # ── 초기 로드 ──
        b.navigate(base + "/")
        b.js(f"localStorage.removeItem({json.dumps(K)}); true")
        b.reload()
        h = b.js("document.getElementById('health').textContent")
        c.check("초기 로드: 서버 상태 표시", h == "준비됨", f"#health = {h!r}")

        # ── PDF 거부 ──
        b.set_file(assets["pdf"])
        err = b.wait("!document.getElementById('error').hidden && document.getElementById('error-msg').textContent", 5)
        dis, hid = b.js("document.getElementById('analyze').disabled"), b.js("document.getElementById('preview').hidden")
        c.check("[image_input] 이미지가 아닌 파일(PDF)을 화면이 거부한다", bool(err) and "이미지" in err and dis and hid,
                f"오류 문구={err!r}, 분석 버튼 disabled={dis}, 미리보기 숨김={hid}")

        # ── 4000px 세로 사진(EXIF 회전) ──
        b.set_file(assets["exif"])
        cap = b.wait("!document.getElementById('preview').hidden && document.getElementById('preview-cap').textContent.includes('3000') && document.getElementById('preview-cap').textContent", 30)
        px = b.js(PIXELS)
        upright = px["top"][0] > 150 and px["top"][2] < 100 and px["bottom"][2] > 150 and px["bottom"][0] < 100
        c.check("[image_input] 4000px 세로 사진이 768px로 줄고 똑바로 선다", px["w"] == 576 and px["h"] == 768 and upright,
                f"4000×3000+EXIF6 → 미리보기 {px['w']}×{px['h']}, 위 픽셀 {px['top']}(빨강 기대), 아래 {px['bottom']}(파랑 기대); 캡션={cap!r}")

        # ── 붙여넣기 · 드롭 ──
        seen = b.js(PASTE)
        cap = b.wait("document.getElementById('preview-cap').textContent.includes('320×200') && document.getElementById('preview-cap').textContent", 10)
        c.check("[image_input] 붙여넣기(Ctrl+V)로 스크린샷이 들어간다", bool(cap), f"합성 paste 이벤트에 파일 실림={seen}, 캡션={cap!r}")
        b.js(DROP)
        cap = b.wait("document.getElementById('preview-cap').textContent.includes('240×160') && document.getElementById('preview-cap').textContent", 10)
        c.check("(참고) 드래그 앤 드롭으로 사진이 들어간다", bool(cap), f"캡션={cap!r}")

        # ── 분석 ──
        photo = a.photo if a.photo.is_file() else assets["landscape"]
        before = b.js("document.getElementById('preview-cap').textContent")
        b.set_file(photo)
        cap = b.wait(f"document.getElementById('preview-cap').textContent !== {json.dumps(before)} && document.getElementById('preview-cap').textContent", 20)
        if not real:
            b.fake("/api/vision", FAKE_VISION, delay=8000)     # 경과 초를 볼 수 있게 8초 늦게 답한다
        b.js("window.__fetches = []; true")
        t0 = time.time()
        bc.click(b, "analyze", 3)
        dis_now, cancel_vis = b.js("document.getElementById('analyze').disabled"), b.js("!document.getElementById('cancel').hidden")
        file_dis = b.js("document.getElementById('file').disabled")
        live = (b.js("document.getElementById('status').textContent"), b.js("document.getElementById('status').getAttribute('aria-live')"))
        time.sleep(3); e1 = b.js("document.getElementById('elapsed').textContent")
        time.sleep(4); e2 = b.js("document.getElementById('elapsed').textContent")
        b.shot("step1_loading.png")
        state = b.wait(bc.STATUS_DONE, 90, 0.5)
        took = time.time() - t0
        n_fetch = b.fetch_count("/api/vision")
        same = b.js("(() => { const f = window.__fetches.find(f => f.url.includes('/api/vision')); return !!f && !!f.body && f.body.image === document.getElementById('preview-img').src; })()")
        status = b.js("document.getElementById('status').textContent")
        err_vis = b.js("!document.getElementById('error').hidden && document.getElementById('error-msg').textContent")
        groups = b.js("Array.from(document.querySelectorAll('#groups .group h3')).map(h => h.textContent)")
        n_items = b.js("document.querySelectorAll('.item').length")
        stored = b.ls(K) or {}
        c.check("[ui_state] 분석 중 버튼이 비활성화되고 두 번 눌리지 않는다", dis_now and n_fetch == 1,
                f"클릭 직후 analyze.disabled={dis_now}, 파일 선택 disabled={file_dis}, 취소 보임={cancel_vis}, 3번 클릭 → /api/vision {n_fetch}회")
        c.check("[ui_state] 경과 초가 올라간다", bc.secs(e1) is not None and bc.secs(e2) is not None and bc.secs(e2) > bc.secs(e1),
                f"3초 뒤 {e1!r} → 7초 뒤 {e2!r}; 상태 문구={live[0]!r} (aria-live={live[1]})")
        idx = [bc.CATEGORIES.index(g.split(" (")[0]) for g in groups]
        c.check(f"[step1] 냉장고 사진을 올리면 재료 목록이 카테고리별로 뜬다{tag}",
                state == "done" and n_items > 0 and idx == sorted(idx) and len(stored.get("items", [])) == n_items,
                f"{took:.1f}초, 상태={state} {status!r}, 오류={err_vis!r}, 그룹={groups}, 항목 {n_items}개(저장소 {len(stored.get('items', []))}개)")
        c.check("[image_input] 미리보기에 보이는 그림과 모델이 받는 그림이 같다", same, f"보낸 image == 미리보기 img.src: {same}; 캡션={cap!r}")
        b.shot("step1_done.png")
        b.unfake()

        # ── 추가·수정·삭제 + F5 ──
        if n_items < 3:
            b.set_list(bc.BROKEN_LIST); b.reload()
        names0 = b.js("Array.from(document.querySelectorAll('.item .i-name')).map(i => i.value)")
        moved = b.js("""(() => { const li = document.querySelectorAll('.item')[1]; const s = li.querySelector('.i-cat');
            const name = li.querySelector('.i-name').value; s.value = '해산물'; s.dispatchEvent(new Event('change', { bubbles: true })); return name; })()""")
        b.js("""(() => { const li = document.querySelector('.item'); const n = li.querySelector('.i-name');
            n.value = '수정한재료'; n.dispatchEvent(new Event('change', { bubbles: true }));
            const q = li.querySelector('.i-qty'); q.value = '7개'; q.dispatchEvent(new Event('change', { bubbles: true })); return true; })()""")
        deleted = b.js(f"""(() => {{ const li = Array.from(document.querySelectorAll('.item')).reverse()
              .find(l => !['수정한재료', {json.dumps(moved)}].includes(l.querySelector('.i-name').value));
            const name = li.querySelector('.i-name').value; li.querySelector('.i-del').click(); return name; }})()""")
        bc.add_item(b, "대파", "2대", "채소")
        b.reload()
        rows = b.js("Array.from(document.querySelectorAll('.item')).map(li => [li.querySelector('.i-name').value, li.querySelector('.i-qty').value, li.querySelector('.i-cat').value])")
        src = {i["name"]: i["source"] for i in b.ls(K)["items"]}
        find = lambda n: next((r for r in rows if r[0] == n), None)
        c.check("[step1·storage] 항목 추가·수정·삭제가 되고 F5 후에도 유지된다",
                find("수정한재료") and find("수정한재료")[1] == "7개" and (find(moved) or [0, 0, 0])[2] == "해산물"
                and ["대파", "2대", "채소"] in rows and len(rows) == len(names0)
                and sum(r[0] == deleted for r in rows) == names0.count(deleted) - 1 and src.get("수정한재료") == src.get("대파") == "user",
                f"F5 후: 수정 {find('수정한재료')}, 카테고리 이동 {find(moved)}, 추가 {find('대파')}, 삭제한 {deleted!r} 남은 수 "
                f"{sum(r[0] == deleted for r in rows)}(원래 {names0.count(deleted)}), 항목 {len(names0)}→{len(rows)}")

        # ── 깨진 이름 표시 → 고치면 풀림 ──
        b.set_list(bc.BROKEN_LIST); b.reload()
        row_state = """(() => { const li = Array.from(document.querySelectorAll('.item')).find(l => l.querySelector('.i-name').value === NAME);
            return li && { review: li.classList.contains('review'), badge: !li.querySelector('.badge').hidden }; })()"""
        before_fix = b.js(row_state.replace("NAME", json.dumps("달modification달걀")))
        b.shot("step1_review.png")
        b.js("""(() => { const li = Array.from(document.querySelectorAll('.item')).find(l => l.querySelector('.i-name').value.includes('modification'));
            const n = li.querySelector('.i-name'); n.value = '달걀'; n.dispatchEvent(new Event('change', { bubbles: true })); return true; })()""")
        after_fix = b.js(row_state.replace("NAME", json.dumps("달걀")))
        b.reload()
        after_f5 = b.js(row_state.replace("NAME", json.dumps("달걀")))
        egg = next(i for i in b.ls(K)["items"] if i["name"] == "달걀")
        c.check("[step1·json_contract] 깨진 이름이 버려지지 않고 needs_review로 표시되고, 고치면 표시가 풀린다",
                before_fix and before_fix["review"] and before_fix["badge"] and not after_fix["review"] and after_f5 and not after_f5["review"]
                and egg["needs_review"] is False and egg["source"] == "user",
                f"고치기 전 {before_fix}; 고친 뒤 {after_fix}; F5 뒤 {after_f5}; 저장소 {egg}")

        # ── '무엇인가요?' → items ──
        b.js("""(() => { const li = document.querySelector('#unsure-list li');
            li.querySelector('input').value = '매실청'; li.querySelector('select').value = '양념소스';
            li.querySelector('form').requestSubmit(); return true; })()""")
        st = b.ls(K)
        m = next((i for i in st["items"] if i["name"] == "매실청"), None)
        c.check("(참고) '무엇인가요?' 항목에 이름을 붙이면 items로 올라간다",
                m and m["category"] == "양념소스" and m["source"] == "user" and st["unsure"] == [], f"items에 {m}, unsure={st['unsure']}")

        # ── 480px ──
        b.viewport(480)
        b.set_list(bc.BROKEN_LIST); b.reload()
        lay = bc.overflow(b)
        p = b.shot("step1_480.png")
        c.check("[ui_state] 480px에서 레이아웃이 유지된다", lay["scroll"] <= lay["inner"] and not lay["over"],
                f"innerWidth={lay['inner']}, scrollWidth={lay['scroll']}, 넘친 요소={lay['over']}, 스크린샷={p.name}")
        b.viewport(1280)

        # ── 깨진 localStorage ──
        n0 = len(b.cdp.events)
        b.js(f"localStorage.setItem({json.dumps(K)}, '{{'); true")
        b.reload()
        empty = b.js("!document.getElementById('empty').hidden && document.querySelectorAll('.item').length === 0")
        exc = b.exceptions_since(n0)
        c.check("[storage] localStorage를 손으로 깨뜨려도 앱이 죽지 않는다", empty and not exc,
                f"{K}='{{' 후 F5: 빈 목록 안내 보임={empty}, JS 예외={exc}")

        # ── 저장소 차단 ──
        sid = b.cdp.call("Page.addScriptToEvaluateOnNewDocument", source=BLOCK_STORAGE)["identifier"]
        n0 = len(b.cdp.events)
        b.reload()
        bc.add_item(b, "차단테스트", "", "기타")
        cnt, h = b.js("document.querySelectorAll('.item').length"), b.js("document.getElementById('health').textContent")
        exc = b.exceptions_since(n0)
        c.check("[storage] 저장소가 막힌 환경에서도 화면이 뜬다(저장만 안 될 뿐)", cnt == 1 and h == "준비됨" and not exc,
                f"화면 상태={h!r}, 직접 추가 후 항목 {cnt}개, JS 예외={exc} (실제 시크릿 창이 아니라 localStorage 접근이 던지게 만든 모의 환경)")
        b.cdp.call("Page.removeScriptToEvaluateOnNewDocument", identifier=sid)

        # ── 식재료 없는 사진 ──
        b.navigate(base + "/")
        b.set_file(assets["landscape"])
        b.wait("!document.getElementById('preview').hidden && !document.getElementById('analyze').disabled", 10)
        if not real:
            b.fake("/api/vision", FAKE_EMPTY)
        bc.click(b, "analyze")
        state = b.wait(bc.STATUS_DONE, 90, 0.5)
        status = b.js("document.getElementById('status').textContent")
        alive = b.js("!document.getElementById('analyze').disabled")
        c.check(f"[step1] 식재료가 없는 사진을 올리면 '찾지 못했습니다'가 뜨고 앱이 죽지 않는다{tag}",
                state == "done" and "찾지 못했습니다" in status and alive, f"상태={state}, 문구={status!r}, 이후 분석 버튼 사용 가능={alive}")

        # ── NO_KEY ──
        (bc.WORK / "fakehome").mkdir(exist_ok=True)
        srv, url = bc.start_server(env_extra={"HOME": str(bc.WORK / "fakehome")}, unset_key=True)
        extra.append(srv)
        b.navigate(url + "/")
        h = b.js("document.getElementById('health').textContent")
        b.set_file(assets["landscape"])
        b.wait("!document.getElementById('analyze').disabled", 10)
        bc.click(b, "analyze")
        msg = b.wait("!document.getElementById('error').hidden && document.getElementById('error-msg').textContent", 15)
        retry_hidden = b.js("document.getElementById('retry').hidden")
        c.check("[ui_state] NO_KEY 오류에는 재시도 버튼이 없다", bool(msg) and "키" in msg and retry_hidden,
                f"상단 상태={h!r}, 오류={msg!r}, 재시도 버튼 숨김={retry_hidden}")

        # ── 프록시를 끄고 분석 ──
        srv, url = bc.start_server()
        extra.append(srv)
        b.navigate(url + "/")
        b.set_file(assets["landscape"])
        b.wait("!document.getElementById('analyze').disabled", 10)
        bc.stop(srv)
        bc.click(b, "analyze")
        msg = b.wait("!document.getElementById('error').hidden && document.getElementById('error-msg').textContent", 15)
        retry_vis = b.js("!document.getElementById('retry').hidden")
        c.check("[step1·ui_state] 프록시를 끄고 분석하면 한국어 오류 한 줄과 재시도 버튼이 뜬다",
                bool(msg) and "연결할 수 없습니다" in msg and retry_vis, f"오류={msg!r}, 재시도 버튼 보임={retry_vis}")

        # ── 보안 ──
        ok, evidence, console = b.security()
        c.check("[보안] 응답·콘솔·네트워크 탭 어디에도 sk-or-v1-이 없다", ok, evidence)
        print(f"\n참고 — 콘솔 메시지(중복 제거): {console or '없음'}")
    finally:
        for p in extra:
            bc.stop(p)
        bc.stop(server)
        b.close()
    return c.summary("1단계 브라우저 점검")


if __name__ == "__main__":
    raise SystemExit(main())
