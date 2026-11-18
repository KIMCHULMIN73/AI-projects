# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 — 한눈 PDF 요약

PDF를 올리면 글자를 뽑아내고, [요약하기]를 누르면 AI가 한 줄 요약·요약 문단·핵심 요점(3~7개)을 한국어로 돌려주는 웹 앱. **사양은 `PRD.md`(왜·무엇·일정)와 `SPEC.md`(구현 계약 — HTTP·모듈 함수·수치·화면 문구·시험 체크리스트)가 원본이다.**

이 폴더는 `std05/`(AI 공감 다이어리) 아래에 있지만 별개 앱이다. 리포지토리 관례·키 규칙·환경은 `../CLAUDE.md`를 따른다. 위 폴더에 같은 이름의 `config.py`·`llm.py`·`server.py`가 있으므로 `tools/` 스크립트는 `sys.path` 맨 앞에 이 폴더를 넣고 불러온 모듈이 이 폴더 것인지 확인한다(SPEC §1).

```
./run.sh                 서버(127.0.0.1:8030 고정) + 브라우저. 이미 떠 있으면 브라우저만, 다른 프로그램이 8030을 쓰면 종료코드 1, Ctrl+C로 종료
NO_OPEN=1 ./run.sh       서버만
python3 -B config.py     키 진단
```

다이어리(8020)와 동시에 띄울 수 있다. `index.html`을 파일로 직접 열면(`file://`) 안내만 나온다 — 키가 서버에만 있어야 해서 의도한 것이다.

## 구조

```
브라우저(index.html·style.css·app.js)
  ├─ POST /api/extract   (원본 바이트 + X-Filename) ─▶ server.py ─▶ pdf_extract.py ─▶ pdfinfo·pdftotext(stdin)
  └─ POST /api/summarize ({text, meta})             ─▶ server.py ─▶ llm.py ─▶ OpenRouter(무료 모델)
```

- 추출과 요약을 두 요청으로 나눴다: 진행 단계를 실제대로 보여주고, 사용자가 [요약하기]를 눌러야만 무료 한도를 쓰고, 요약 재시도 때 파일을 다시 올리지 않는다.
- **PDF 추출은 시스템 도구 `pdftotext`·`pdfinfo`(poppler-utils)** — 외부 의존성 금지 관례의 예외(파이썬 PDF 추출 패키지가 없음). stdin으로 넘기고 디스크에 쓰지 않으며 `shell=True` 금지, 한 번 30초 제한. 도구가 없으면 `TOOL_MISSING`, 다른 기기에서는 poppler-utils 설치가 필요하다.
- 상한: 파일 20MiB, 300쪽, 본문 60,000자(넘으면 쪽 단위로 잘라 앞부분만 요약하고 화면에 알림), 공백 제외 20자 미만이면 `NO_TEXT`(스캔본 — OCR은 범위 밖).
- `server.py`는 `llm.summarize(...)`처럼 모듈 속성으로 부른다(시험 때 가짜로 바꿔 끼움). `llm` import가 실패해도 서버는 뜨고 요약만 `INTERNAL`.
- 요약은 동시에 1건(`BUSY` 409, 2초 기다린 뒤). 화면에서 취소·새로고침하면 서버가 연결 끊김을 감지해(`should_stop`) 모델 호출을 멈추고 잠금을 놓는다.
- Host(`127.0.0.1:8030`·`localhost:8030`)·Origin(같은 출처만, `null` 거부) 검사, 정적 파일은 허용 목록만, CSP. 로그에는 파일 이름·제목·본문·키를 남기지 않는다(시도 기록 `trail`만).

## 모델 — 무료(`:free`)만 (2026-11-17 ai-integration-expert 선택, QA 확인)

- 1순위 `baidu/ernie-5.5-flash-preview:free`(`reasoning: {effort: low, exclude: true}` — 기본 추론보다 2배 빠르고 품질 같음), 폴백 `nvidia/nemotron-3.5-super-vl:free`(다른 공급자). ling은 타임아웃, dots는 중국어·일본어가 섞여 제외. `llm.py`는 `:free`가 아닌 ID면 멈춘다 — 이 계정은 크레딧이 있어 유료 모델은 실제로 과금된다.
- **`CHUNK_CHARS` = 60,000(사실상 나누지 않음)**: 두 모델 context가 약 100만 토큰. 실측(한국어 56,412자): 2만 자 분할 4회 29.9초 vs 한 번에 1회 15.9초, 한 번에 쪽이 더 구체적. 분할 코드는 남아 있고 `check_llm`이 2만 자로 바꿔 계속 시험한다.
- `finish_reason=length`(ernie 추론 폭주, 실측 3/16회)는 폴백 모델로 넘긴다 — 같은 모델 재시도는 같은 폭주를 되풀이한다. 하루 한도 429(`free-models-per-day`)는 폴백 없이 바로 `DAILY_LIMIT` + `reset_at`.
- **무료 모델은 계정 전체 하루 50회 한도**를 다이어리와 함께 쓴다(09:00 KST 초기화).
- 실측: 추출 0.1~0.6초, 요약 짧은 문서 4~14초(폴백이 끼면 18~20초), 3~6만 자 7~22초.

## 검증 (모두 `python3 -B`, 시험 PDF·스크린샷은 저장소 밖 DIR)

```
tools/make_test_pdfs.py --out DIR [--real]          시험 PDF 19종(+ 이 기기의 실제 문서 5종). 저장소 안 폴더는 거부
tools/check_server.py --pdfs DIR                    HTTP 계약 169항목 (가짜 llm, 포트 8032)
tools/check_llm.py                                   재시도·폴백·분할·취소·하루 한도 92항목 (가짜 업스트림)
tools/check_frontend.py --out DIR                   화면 126항목 (tools/mock_server.py, 8031)
tools/check_e2e.py --out DIR [--pdfs DIR]           실제 server.py + 실제 화면 + 가짜 업스트림(8033) 90항목
tools/check_e2e.py --out DIR --live [--docs ko.pdf,en.pdf] [--max-calls 6]   run.sh 실제 실행 + 실제 모델 + 키 누출 검사
tools/check_firefox.py --out DIR                    Firefox(WebDriver BiDi) 업로드→요약→복사 8항목
tools/live_summaries.py --out DIR [--chunk N] FILE…   실제 요약 품질·시간 비교 (호출 수 DIR/live_calls.jsonl)
tools/compare_models.py --list                      무료 모델 목록(호출 없음)
```

실제 모델을 부르는 것은 `--live`, `live_summaries.py`, `compare_models.py`(--list 제외)뿐이다. 나머지는 몇 번 돌려도 한도를 쓰지 않는다.

## 서브에이전트 분담 (1단계 파일 소유권 — SPEC §1)

| 담당 | 파일 |
|---|---|
| backend-developer | `server.py`, `pdf_extract.py`, `run.sh`, `config.py`(std05 것 복사), `.env.example`, `tools/make_test_pdfs.py`, `tools/check_server.py` |
| ai-integration-expert | `llm.py`, `tools/check_llm.py`, `tools/compare_models.py` |
| frontend-developer | `index.html`, `style.css`, `app.js`, `tools/mock_server.py`, `tools/check_frontend.py` |
| quality-assurance-engineer | `tools/check_e2e.py` 등 통합 점검. 2단계에서는 사용자 지시로 앱 코드 전체를 고쳤다 |
| product-manager | `PRD.md`, `SPEC.md` |

## 작업 로그

- **2026-11-17** — 사용자가 에이전트별로 일을 나눠 지시. product-manager가 PRD·SPEC(파일 소유권·모듈 계약·HTTP 계약)을 먼저 쓰고, backend·ai·frontend가 동시에 구현, QA가 처음 붙여 시험하며 6건을 고쳤다: 요약 취소·새로고침 뒤에도 서버가 모델 호출을 계속해 409 BUSY(→ 연결 끊김 감지·`should_stop`), 긴 문서 4회 호출(→ 조각 60,000자로 1회), 재시도 원인이 로그에 없음(→ `trail`), 명령어가 낱말 중간에서 끊김, file:// 화면의 불필요한 스크롤, 시험 도구 보강. 실제 모델 호출 ai 14회 + QA 19회, 모두 무료. 미결 사항(PRD U1~U11)은 사용자 답이 없어 기본값으로 구현했다.
- **2026-11-17** — 사용자 결정: [읽기 취소]·[요약 취소] 유지(PRD U13). "글자가 없는 쪽 n개는 빼고 읽었어요"의 n은 요약 범위(`pages_used`) 안의 빈 쪽만 센다(PRD U12 (a), `pdf_extract.py`) — 시험 `long_scan_ends.pdf`.
- **2026-11-17~18 — 3단계(추출한 텍스트 보기 R15 + 그림·표가 든 HTML 다운로드 R16) 구현까지 완료, 4단계(QA 통합 시험) 남음.** 사양은 PRD 3·4단계, SPEC §10. backend: `pdf_layout.py`(새), `server.py`의 `/api/layout`·`/api/export`, 시험 PDF `LAYOUT_CASES`(manifest_layout.json), `tools/check_layout.py`(새) — check_server 267/267, check_layout --real 141/141. frontend: 문서 카드의 두 버튼, `viewer.html/css/js`(새, BroadcastChannel로 File 전달 — chromium·Firefox 실측), `tools/check_viewer.py`(새) 118/118, check_frontend 126/126. 모델 호출 0회(이 기능은 모델을 안 씀). 11-17 17시경 Claude 사용량 한도로 두 에이전트가 끊겼다가 11-18에 이어서 끝냄.
  - **내일(4단계) QA가 할 일**: 실제 server.py로 보기 창·다운로드 통합(Host·Origin·`LAYOUT_SLOTS` BUSY), Firefox 전체 흐름, 실제 Chrome의 연속 다운로드 허용 질문, 300쪽·그림 많은 문서 화면 성능, 표 오탐(나란한 그림 설명 쌍·문법 줄이 표로 잡힘, 여러 줄 셀은 첫 줄만) 개선 여부, `check_e2e`에 보기·다운로드 추가. 사양과 어긋난 점(`pdftohtml -dataurls` segfault → `pdftocairo -svg` 대안 경로, 회전 쪽 좌표 정규화 등)은 product-manager가 SPEC에 반영할 것.
  - 8030에 떠 있는 앱 서버는 3단계 이전 코드로 시작한 것이다 — 시험 전에 다시 띄울 것.
- 남은 확인: 실제 OpenRouter `DAILY_LIMIT`(가짜로만 확인), 실제 마우스 드래그, 화면 낭독기, 한글 IME, ernie 추론 폭주 대책(1순위 교체 또는 `reasoning.max_tokens`).
