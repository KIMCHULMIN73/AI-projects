# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 — 한눈 PDF 요약

PDF를 올리면 글자를 뽑아내고, [요약하기]를 누르면 AI가 한 줄 요약·요약 문단·핵심 요점(3~7개)을 한국어로 돌려주는 웹 앱. 문서 카드의 [추출한 텍스트 보기]는 원본에 가깝게(제목·표·그림·목차) 새 창(`viewer.html`)에 보여주고, [추출한 텍스트 다운로드하기]는 그림·표가 든 `.html` 한 파일을 내려받는다(모델을 쓰지 않음). **사양은 `PRD.md`(왜·무엇·일정)와 `SPEC.md`(구현 계약 — HTTP·모듈 함수·수치·화면 문구·시험 체크리스트)가 원본이다.**

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
tools/check_server.py --pdfs DIR                    HTTP 계약 267항목 (가짜 llm, 포트 8032)
tools/check_layout.py --pdfs DIR [--real]          pdf_layout 모듈 157항목 (표·그림·목차·글자 보존·멈춤). pdftohtml 등을 쓰는 다른 작업과 동시에 돌리면 임시 폴더 점검이 겹쳐 실패할 수 있다
tools/check_llm.py                                   재시도·폴백·분할·취소·하루 한도 92항목 (가짜 업스트림)
tools/check_frontend.py --out DIR                   화면 126항목 (tools/mock_server.py, 8031)
tools/check_e2e.py --out DIR [--pdfs DIR] [--part all|summary|viewer] [--extra 그림많은.pdf,…]   실제 server.py + 실제 화면 + 가짜 업스트림(8033) 약 160항목(요약 90 + 보기·다운로드 70), 측정 DIR/viewer/report.json
tools/check_e2e.py --out DIR --live [--docs ko.pdf,en.pdf] [--max-calls 6]   run.sh 실제 실행 + 실제 모델 + 키 누출 검사
tools/check_firefox.py --out DIR                    Firefox(WebDriver BiDi) 업로드→요약→복사·보기 창·다운로드 15항목
tools/check_viewer.py --out DIR                     보기 창·다운로드 118항목 (mock_server 8031)
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
- **2026-11-17~18 — 3단계 구현**: 사용자 요청으로 [추출한 텍스트 보기](R15)·그림·표가 든 HTML 다운로드(R16, txt는 그림·표가 빠져 사용자가 거절). PM이 PRD 3·4단계·SPEC §10을 쓰고 backend(`pdf_layout.py`, `/api/layout`·`/api/export`, `LAYOUT_CASES` 시험 PDF)와 frontend(두 버튼, `viewer.html/css/js` — BroadcastChannel로 File 전달)가 동시에 구현. 11-17 17시경 Claude 사용량 한도로 끊겼다가 11-18에 이어서 끝냄. 커밋 3cc32e5.
- **2026-11-19 — 4단계 QA 통합 시험, 7건 수정**: 표 판정(여러 줄 셀 잇기, 그림 설명 쌍·빈 칸 많은 블록 거부, 머리글과 어긋난 열 — 실제 문서 3종 표 43→21개, 명확한 오탐 25→2개, 글자 손실 0), 보기 창을 닫아도 서버가 끝까지 만들며 처리 자리를 붙잡음(→ `should_stop`·`CLIENT_GONE`), run.sh Ctrl+C 뒤 서버 종료를 기다리지 않음, 보기 창 마지막 쪽 이동 시 쪽 번호가 되돌아감(`pinPage`), 시험 도구 3건. 연속 다운로드는 클릭마다 시작하므로 Chrome "여러 파일 다운로드" 질문이 뜨지 않음(실제 제한기로 확인). Firefox 전체 흐름 통과. 실제 모델 호출 1회(최종 요약 확인).
  - 표 판정의 남은 오탐: 양쪽 맞춤 문단의 넓은 띄어쓰기(xz 18쪽), 문장 안 그림(rpi 85쪽). 좌표만으로는 좁은 열 표와 구별할 수 없다 — 열 사이 빈 띠 최소폭 규칙은 xz Preset 표를 다시 깨서 되돌렸다.
  - 글자가 아주 많은 290쪽 문서는 불러오는 동안 긴 작업(최대 1.2초)이 생긴다. `content-visibility: auto`는 목차 이동 위치가 어긋날 위험이 있어 보류.
  - `pdftohtml -dataurls`는 평범한 쪽에서도 죽는다(segfault) → `pdftocairo -svg`로 그림 상자를 대신 구한다(SPEC §10.5 7-b).
- 남은 확인: 헤드풀 Chrome 눈 확인, 사람이 원본과 나란히 비교, 실제 OpenRouter `DAILY_LIMIT`(가짜로만 확인), 실제 마우스 드래그, 화면 낭독기, 한글 IME, ernie 추론 폭주 대책(1순위 교체 또는 `reasoning.max_tokens`).
