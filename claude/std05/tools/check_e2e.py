#!/usr/bin/env python3
"""통합 점검: 실제 server.py + 실제 프런트(index.html·style.css·app.js)를 headless Chromium으로 끝까지.
개발 전용 — 앱은 이 파일을 로드하지 않는다.

    python3 -B tools/check_e2e.py [--out 스크린샷_폴더]          # 기본: 실제 모델 호출 0회
    python3 -B tools/check_e2e.py --live [--out 스크린샷_폴더]   # ./run.sh로 띄워 실제 모델 2회

기본 모드
  server.py의 Handler·Server를 이 프로세스에서 그대로 띄우고(빈 포트, ALLOWED_HOSTS에 추가),
  OpenRouter만 가짜로 바꾼다(llm.API_URL·config.auth_header 교체 — 진짜 키는 어디로도 안 나간다).
  입력 → 대기 → 결과 → 지난 일기 저장 → 새로고침 후 유지 → 삭제, 위기(모델 성공·모델 실패 둘 다),
  429·타임아웃·키 없음·깨진 출력 뒤 재시도 회복, 서버 꺼짐→다시 켬, 대기 중 새로고침, 연속 제출,
  HTML 입력·HTML 섞인 모델 답, 일기 500개 성능, 1280·480·360px 스크린샷·가로 넘침, 키보드만으로 사용,
  첫 화면 로드 시간, 콘솔 오류 0.
--live 모드
  NO_OPEN=1 ./run.sh로 127.0.0.1:8020에 실제 서버를 띄우고 실제 모델로 일반·위기 입력을 하나씩 보낸다.
  run.sh 로그·정적 파일·응답에 키가 없는지, Ctrl+C(프로세스 그룹 SIGINT)로 서버까지 꺼지고 8020이 비는지.
스크린샷은 저장소 밖(기본: 시스템 임시 폴더/std05-e2e-check)에 남긴다. 끝나면 서버·브라우저를 끈다.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import check_frontend as cf  # noqa: E402  (CDP 도구 재사용)
import config  # noqa: E402
import llm  # noqa: E402
import server  # noqa: E402

LIVE = "--live" in sys.argv
OUT = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else Path(tempfile.gettempdir()) / "std05-e2e-check"
cf.OUT = OUT
KEY = "diary.v1.entries"

results: list[tuple[str, bool]] = []


def check(name: str, ok, evidence="") -> None:
    results.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n       {evidence}" if evidence and not ok else ""), flush=True)


# ── 가짜 OpenRouter ─────────────────────────────────────
PLAN: list[str] = []          # 호출마다 하나씩 꺼낸다. 비면 "ok"
CALLS: list[str] = []
LOG: list[str] = []


def diary_of(body: dict) -> str:
    user = body["messages"][1]["content"]
    return json.loads(user.split("<diary>", 1)[1].split("</diary>", 1)[0])


class FakeUpstream(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert body["model"].endswith(":free"), body["model"]
        CALLS.append(body["model"])
        text = diary_of(body)
        step = PLAN.pop(0) if PLAN else "ok"
        status, payload = 200, None
        if step.startswith("slow"):
            time.sleep(float(step[4:] or 3))
            step = "ok"
        if step in ("ok", "crisis", "html"):
            obj = {"emotion": "기쁨", "intensity": 4, "crisis": False,
                   "message": f"'{text[:20]}'라고 적어 주셨네요. 오늘 하루 정말 수고 많으셨어요."}
            if step == "crisis":
                obj.update(emotion="슬픔", intensity=5, crisis=True,
                           message="많이 힘드셨겠어요. 혼자 견디지 마시고 주변이나 전문 상담에 도움을 요청해 주세요.")
            if step == "html":
                obj["message"] = "<img src=x onerror=\"window.__xss2=1\"><b>굵게</b> 마음이 전해져요. 오늘도 고생하셨어요."
            payload = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(obj, ensure_ascii=False)}}]}
        elif step == "garbage":
            payload = {"choices": [{"finish_reason": "stop", "message": {"content": "죄송하지만 답할 수 없습니다."}}]}
        elif step == "429":
            status, payload = 429, {"error": {"code": 429, "message": "rate limited"}}
        elif step == "500":
            status, payload = 500, {"error": {"code": 500, "message": "boom"}}
        raw = json.dumps(payload, ensure_ascii=False).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            pass


class KillableServer(server.Server):
    """실제 server.Server와 같되, kill()로 열린 keep-alive 연결까지 끊는다(프로세스가 죽은 것처럼)."""

    def process_request(self, request, client_address):
        self.__dict__.setdefault("conns", []).append(request)
        super().process_request(request, client_address)

    def kill(self):
        self.shutdown()
        self.server_close()
        for c in self.__dict__.get("conns", []):
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def start(handler, port=0) -> ThreadingHTTPServer:
    srv = KillableServer(("127.0.0.1", port), handler) if handler is server.Handler else ThreadingHTTPServer(("127.0.0.1", port), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ── 화면 도우미 ─────────────────────────────────────────
READY = "document.documentElement.classList.contains('app-ready')"
IDLE_DONE = ("document.getElementById('waiting').hidden && (!document.getElementById('result').hidden"
             " || !document.getElementById('error-box').hidden)")


def txt(b, id_):
    return " ".join((b.js(f"document.getElementById('{id_}').textContent") or "").split())


def n_items(b):
    return b.js("document.querySelectorAll('#history-list > li').length")


def stored(b):
    return b.js(f"(() => {{ try {{ return JSON.parse(localStorage.getItem('{KEY}')) || [] }} catch (e) {{ return 'ERR' }} }})()")


def submit(b, text):
    cf.submit(b, text)


def done(b, timeout=15):
    b.wait(IDLE_DONE, timeout)


def plan(*steps):
    PLAN.clear()
    PLAN.extend(steps)
    CALLS.clear()


def crisis_links(b):
    return b.js("[...document.querySelectorAll('#result-crisis a')].map(a => a.getAttribute('href'))")


def nav_timing(b):
    return b.js("(() => { const n = performance.getEntriesByType('navigation')[0];"
                " return {dcl: n.domContentLoadedEventEnd, load: n.loadEventEnd}; })()")


# ── 기본 모드(가짜 업스트림) ──────────────────────────────
def run_fake() -> None:
    up = start(FakeUpstream)
    llm.API_URL = f"http://127.0.0.1:{up.server_address[1]}/v1/chat/completions"
    real_auth = config.auth_header
    config.auth_header = lambda **extra: {"Authorization": "Bearer test-dummy", "Content-Type": "application/json", **extra}
    server.log = lambda line: LOG.append(config.redact(line))
    app = start(server.Handler)
    port = app.server_address[1]
    server.ALLOWED_HOSTS.add(f"127.0.0.1:{port}")
    base = f"http://127.0.0.1:{port}/"
    b = cf.Browser()
    try:
        b.call("Page.addScriptToEvaluateOnNewDocument", source=cf.SPY)
        b.viewport(1280)

        # 1. 첫 화면 + 로드 시간
        loads = []
        for _ in range(5):
            b.goto(base, READY)
            loads.append(nav_timing(b))
        dcl = [round(x["dcl"]) for x in loads]
        check("첫 화면: 실제 서버에서 app-ready·빈 목록", b.js(READY) and b.js("!document.getElementById('history-empty').hidden"))
        print(f"       첫 화면 DOMContentLoaded(ms) 5회 {dcl} 중앙값 {statistics.median(dcl)} 최대 {max(dcl)}")

        # 2. 입력 → 대기 → 결과 → 저장 → 새로고침 유지 → 삭제
        plan("slow1.5")
        submit(b, "오랜만에 친구랑 통화하면서 한참 웃었어요")
        check("제출 직후 대기 화면·버튼 잠김", b.wait("!document.getElementById('waiting').hidden", 2)
              and b.js("document.getElementById('entry-submit').disabled"))
        done(b)
        check("결과 표시(감정·세기·답장)", txt(b, "result-emotion") == "기쁨" and "5단계 중 4" in txt(b, "result-intensity")
              and "수고" in txt(b, "result-message"), txt(b, "result"))
        check("지난 일기 1편·저장소 1편·입력칸 비움", n_items(b) == 1 and len(stored(b)) == 1
              and b.js("document.getElementById('entry-input').value") == "")
        b.goto(base, READY)
        check("새로고침 뒤에도 지난 일기 유지", n_items(b) == 1 and "한참 웃었어요" in txt(b, "history-list"))
        b.js("document.querySelector('#history-list [data-action=ask-delete]').click()")
        b.js("document.querySelector('#history-list [data-action=confirm-delete]').click()")
        b.goto(base, READY)
        check("삭제 → 새로고침 뒤에도 0편", n_items(b) == 0 and stored(b) == [])

        # 3. 위기: 모델이 crisis를 줄 때 / 모델이 실패할 때(키워드 안전망 → 200 고정 응답)
        plan("crisis")
        submit(b, "요즘 너무 힘들어서 죽고 싶어")
        done(b)
        check("위기(모델 판정): 109·1577-0199 표시", crisis_links(b) == ["tel:109", "tel:1577-0199"], crisis_links(b))
        for label, steps in [("429", ("429", "429")), ("502", ("500", "500")), ("깨진 출력", ("garbage", "garbage"))]:
            plan(*steps)
            submit(b, "그냥 다 끝내고 싶다")
            done(b)
            check(f"위기 + 모델 {label}: 오류 대신 결과와 109·1577-0199", b.js("document.getElementById('error-box').hidden")
                  and crisis_links(b) == ["tel:109", "tel:1577-0199"] and txt(b, "result-emotion") == "슬픔", txt(b, "main")[:200])
        saved = (llm.TOTAL_BUDGET_S, llm.PER_CALL_TIMEOUT_S, llm.MIN_CALL_S)
        llm.TOTAL_BUDGET_S, llm.PER_CALL_TIMEOUT_S, llm.MIN_CALL_S = 3.0, 1.0, 0.5
        plan("slow3", "slow3")
        submit(b, "죽 고 싶 다")
        done(b, 10)
        check("위기 + 모델 타임아웃: 결과와 상담 번호", crisis_links(b) == ["tel:109", "tel:1577-0199"])
        check("위기 결과는 지난 일기에도 상담 번호", b.js("document.querySelector('#history-list > li .entry__help') !== null"))

        # 4. 위기 아닌 입력의 오류 → 다시 보내면 회복
        plan("slow3", "slow3")
        submit(b, "오늘은 그냥 평범했다")
        done(b, 10)
        check("타임아웃(504) 안내·입력 유지", "오래 걸려서" in txt(b, "error-text")
              and b.js("document.getElementById('entry-input').value") == "오늘은 그냥 평범했다", txt(b, "error-text"))
        llm.TOTAL_BUDGET_S, llm.PER_CALL_TIMEOUT_S, llm.MIN_CALL_S = saved
        for label, steps, expect in [("429", ("429", "429"), "1분쯤"), ("502", ("500", "500"), "AI 쪽 사정"),
                                     ("깨진 출력", ("garbage", "garbage"), "제대로 만들어지지")]:
            plan(*steps)
            submit(b, "오늘은 그냥 평범했다")
            done(b)
            check(f"{label} 안내 문구", expect in txt(b, "error-text"), txt(b, "error-text"))
        plan("ok")
        b.js("document.getElementById('entry-form').requestSubmit()")
        done(b)
        check("오류 뒤 같은 글 다시 보내면 정상 결과", b.js("document.getElementById('error-box').hidden")
              and not b.js("document.getElementById('result').hidden"))
        config.auth_header = lambda **_: (_ for _ in ()).throw(config.MissingKeyError("없음"))
        plan("ok")
        submit(b, "오늘은 그냥 평범했다")
        done(b)
        check("키 없음(NO_KEY) 안내·모델 호출 없음", "API 키" in txt(b, "error-text") and not CALLS, txt(b, "error-text"))
        submit(b, "죽고 싶어")
        done(b)
        check("키 없음이어도 위기 입력엔 상담 번호", crisis_links(b) == ["tel:109", "tel:1577-0199"])
        config.auth_header = lambda **extra: {"Authorization": "Bearer test-dummy", "Content-Type": "application/json", **extra}

        # 5. HTML 입력·HTML 섞인 모델 답은 글자로만
        plan("html")
        submit(b, '<script>window.__xss=1</script><img src=x onerror="window.__xss=1"> 오늘')
        done(b)
        check("HTML 입력·HTML 답장이 실행·해석되지 않음", b.js("window.__xss") is None and b.js("window.__xss2") is None
              and b.js("document.querySelectorAll('#result img, #history-list img, #result b').length") == 0
              and "<img" in txt(b, "result-message"))

        # 6. 서버 꺼짐 → 안내 → 다시 켜고 재전송
        app.kill()
        submit(b, "서버가 꺼진 동안 쓴 글")
        done(b)
        check("서버 꺼짐: 연결 안내(run.sh)", not b.js("document.getElementById('error-box').hidden")
              and "run.sh" in txt(b, "error-text"), txt(b, "main")[:200])
        app = start(server.Handler, port)
        plan("ok")
        b.js("document.getElementById('entry-form').requestSubmit()")
        done(b)
        check("서버 다시 켠 뒤 재전송 성공", not b.js("document.getElementById('result').hidden")
              and "서버가 꺼진 동안" in txt(b, "result-quote"))

        # 7. 연속 빠른 제출 / 대기 중 새로고침
        plan("slow1")
        count0 = b.js("window.__fetchCount")
        b.js("(() => { const i = document.getElementById('entry-input'); i.value = '연속 제출 시험';"
             " const f = document.getElementById('entry-form'); for (let k = 0; k < 5; k++) f.requestSubmit(); })()")
        b.js("document.getElementById('entry-input').focus()")
        for _ in range(3):
            b.key("Enter", "Enter", 13, "\r")
        done(b)
        time.sleep(0.5)
        check("연속 제출 8번 → 요청 1번·모델 1번", b.js("window.__fetchCount") == count0 + 1 and len(CALLS) == 1,
              f"fetch {b.js('window.__fetchCount') - count0}, 모델 {len(CALLS)}")
        n0 = len(stored(b))
        plan("slow2")
        submit(b, "대기 중 새로고침 시험")
        b.wait("!document.getElementById('waiting').hidden", 2)
        b.goto(base, READY)
        time.sleep(2.5)
        check("대기 중 새로고침: 새 화면 정상·늦은 응답이 기록에 안 들어감",
              b.js(READY) and b.js("document.getElementById('waiting').hidden") and len(stored(b)) == n0
              and not b.js("document.getElementById('entry-submit').disabled"))

        # 8. 1280·480·360 화면
        plan("crisis")
        submit(b, "요즘 너무 지쳐서 사라지고 싶다는 생각도 들어요")
        done(b)
        for w in (1280, 480, 360):
            b.viewport(w, 900)
            time.sleep(0.2)
            ov = cf.overflow(b)
            check(f"{w}px 가로 넘침 없음(실제 서버 답장·위기 카드 포함)", ov["scroll"] <= ov["client"] and not ov["wide"], ov)
            b.shot(f"e2e-{w}")
        b.viewport(1280)

        # 9. 키보드만으로: Tab → 입력 → Enter → 결과 → 지우기
        b.goto(base, READY)
        n0 = n_items(b)
        b.key("Tab", "Tab", 9); b.key("Tab", "Tab", 9)
        focus = b.js("document.activeElement.id")
        b.call("Input.insertText", text="키보드로만 쓴 일기")
        plan("ok")
        b.key("Enter", "Enter", 13, "\r")
        done(b)
        ok_write = focus == "entry-input" and n_items(b) == n0 + 1
        # 지우기 버튼까지 Tab
        for _ in range(60):
            b.key("Tab", "Tab", 9)
            if b.js("document.activeElement.dataset.action") == "ask-delete":
                break
        b.key("Enter", "Enter", 13, "\r")
        on_cancel = b.js("document.activeElement.dataset.action") == "cancel-delete"
        b.key("Tab", "Tab", 9)
        b.key("Enter", "Enter", 13, "\r")
        check("키보드만으로 쓰기·지우기", ok_write and on_cancel and n_items(b) == n0, f"focus={focus} items={n_items(b)}")

        # 10. 일기 500개 성능
        many = [{"id": f"id{i}", "at": f"2026-{1 + i % 12:02d}-{1 + i % 28:02d}T09:00:00Z", "text": "가" * 150,
                 "emotion": llm.EMOTIONS[i % 8], "intensity": 1 + i % 5, "message": "나" * 200 + ".", "crisis": i % 50 == 0}
                for i in range(500)]
        b.js(f"localStorage.setItem('{KEY}', {json.dumps(json.dumps(many, ensure_ascii=False))})")
        t = []
        for _ in range(5):
            b.goto(base, READY)
            t.append(round(nav_timing(b)["dcl"]))
        check("일기 500개: 목록 500편", n_items(b) == 500)
        print(f"       일기 500개 첫 화면 DOMContentLoaded(ms) 5회 {t} 중앙값 {statistics.median(t)} 최대 {max(t)}")
        ms = []
        for k in range(5):
            plan("ok")
            ms.append(b.js(f"""new Promise(res => {{
                const t0 = performance.now();
                const obs = new MutationObserver(() => {{ if (!document.getElementById('result').hidden &&
                  document.getElementById('waiting').hidden) {{ obs.disconnect(); res(Math.round(performance.now() - t0)); }} }});
                obs.observe(document.getElementById('main'), {{subtree: true, attributes: true, childList: true}});
                const i = document.getElementById('entry-input'); i.value = '많을 때 {k}';
                document.getElementById('entry-form').requestSubmit(); }})"""))
        print(f"       일기 500개일 때 제출→결과(ms, 가짜 업스트림) 5회 {ms} 중앙값 {statistics.median(ms)} 최대 {max(ms)}")
        check("일기 500개에서도 제출→결과 중앙값 200ms·최대 1초 이내(가짜 업스트림 기준)",
              statistics.median(ms) < 200 and max(ms) < 1000, ms)

        errs = b.errors()
        check("콘솔 오류 0(앱 코드)", not errs, errs[:5])
        check("서버 로그에 키·가짜 키 없음", not any("sk-or-v1" in l or "test-dummy" in l for l in LOG))
        check("클라이언트가 먼저 끊어도 서버 트레이스백·내부 오류 없음",
              not any("내부 오류" in l or "예외" in l for l in LOG), [l for l in LOG if "오류" in l or "예외" in l][:3])
    finally:
        b.close()
        app.kill()
        up.shutdown(); up.server_close()
        config.auth_header = real_auth


# ── --live 모드(./run.sh + 실제 모델 2회) ──────────────────
def port_busy(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def run_live() -> None:
    if port_busy(8020):
        check("8020이 비어 있음(--live 시작 조건)", False, "이미 무언가 떠 있습니다. 끄고 다시 실행하세요.")
        return
    log_path = OUT / "run.log"
    logf = open(log_path, "w")

    def pre():
        signal.signal(signal.SIGINT, signal.SIG_DFL)   # 터미널에서 실행한 것과 같게
        os.setsid()
    proc = subprocess.Popen([str(ROOT / "run.sh")], env=dict(os.environ, NO_OPEN="1"), preexec_fn=pre,
                            stdout=logf, stderr=subprocess.STDOUT, cwd="/")
    b = None
    base = "http://127.0.0.1:8020/"
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(base, timeout=1); break
            except Exception:
                time.sleep(0.1)
        check("NO_OPEN=1 ./run.sh가 다른 폴더에서 실행해도 8020에 서버를 띄움", port_busy(8020))
        b = cf.Browser()
        b.viewport(1280)
        b.goto(base, READY)
        check("실제 서버: 첫 화면 준비", b.js(READY))
        for text, want_crisis in [("오랜만에 친구랑 통화하면서 한참 웃었어요", False), ("요즘 너무 힘들어서 죽고 싶어", True)]:
            t0 = time.monotonic()
            submit(b, text)
            done(b, 60)
            dt = time.monotonic() - t0
            ok = not b.js("document.getElementById('result').hidden")
            links = crisis_links(b)
            check(f"실제 모델: '{text[:12]}…' 결과 {dt:.1f}s, crisis {'있음' if links else '없음'}",
                  ok and (links == ["tel:109", "tel:1577-0199"]) == want_crisis, txt(b, "main")[:200])
            print(f"       {txt(b, 'result-emotion')} / {txt(b, 'result-intensity')} / {txt(b, 'result-message')}")
        b.goto(base, READY)
        check("실제 서버: 새로고침 뒤 2편 유지", n_items(b) == 2)
        try:
            real_key = config.find_api_key()[0]
        except config.MissingKeyError:
            real_key = None
        statics = b.js("Promise.all(['/', '/app.js', '/style.css'].map(u => fetch(u).then(r => r.text()))).then(a => a.join(''))")
        page = b.js("document.documentElement.outerHTML + JSON.stringify(localStorage)")
        logtext = log_path.read_text()
        leak = [n for n, s in (("정적 파일", statics), ("화면·저장소", page), ("run.sh 로그", logtext))
                if "sk-or-v1" in s or (real_key and real_key in s)]
        check("키가 정적 파일·화면·저장소·로그 어디에도 없음", not leak, leak)
        check("콘솔 오류 0", not b.errors(), b.errors()[:3])
    finally:
        if b:
            b.close()
        os.killpg(proc.pid, signal.SIGINT)              # 터미널 Ctrl+C
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
        logf.close()
        time.sleep(0.3)
        check("Ctrl+C로 run.sh·서버 모두 종료, 8020 비움", not port_busy(8020) and "종료" in log_path.read_text())
        calls = log_path.read_text().count("모델 성공 ") + log_path.read_text().count("모델 실패 [")
        print(f"       실제 모델 호출 {calls}회 (로그 {log_path})")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        run_live() if LIVE else run_fake()
    finally:
        import shutil
        shutil.rmtree(OUT / "chrome-profile", ignore_errors=True)
    passed = sum(ok for _, ok in results)
    print("\n" + "=" * 60)
    print(f"통합 점검({'live' if LIVE else '가짜 업스트림, 실제 모델 0회'}) {passed}/{len(results)} 통과  (스크린샷: {OUT})")
    for name, ok in results:
        if not ok:
            print("  실패:", name)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
