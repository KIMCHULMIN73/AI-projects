---
description: 선생님 모드 5단계를 한 번에 — 반출·수집·통계·비교·대시보드를 순서대로, 단계마다 검증하며 실행한다
argument-hint: "[--refresh 새 데이터만 반영] [--rebuild 처음부터] [--anon 가명] [--dry-run]"
allowed-tools: Bash, Read, Grep, Write, Edit
---

실행 인수: **$ARGUMENTS**

> 이 하네스는 위치 인수(`$1`)를 신뢰할 수 없다 — 0-based로 밀려 들어온다.
> **`$ARGUMENTS` 한 덩어리만 쓰고 옵션은 그 문자열에서 골라낼 것.**

`/teacher-export` → `/teacher-collect` → `/teacher-stats` → `/teacher-compare` → `/teacher-dashboard`를 **순서대로** 돌린다. 각 단계 끝에 **검증**이 붙어 있고, **검증에 실패하면 다음 단계로 넘어가지 않는다.** 실패한 단계 번호와 원인을 보고하고 그 자리에서 멈춘다.

`/quiz-daily`와 같은 규율이다. 앞 단계가 만든 것을 뒤 단계가 그대로 먹는 사슬이라, 중간이 조용히 어긋나면 **마지막 화면에 그럴듯한 거짓 숫자가 뜬다.** 성적표에서 그건 일어나면 안 된다.

## 이 명령은 사람에게 되묻지 않는다

전 단계가 자동으로 끝난다. 판정을 미뤄 사람에게 물어야 할 자리가 없기 때문이다 — **같은 이름을 한 사람으로 볼지 말지를 판정하지 않고, 판마다 따로 세기** 때문이다(`김철민_1`·`김철민_2`). 이름을 뭉치려 들면 "`철민`과 `김철민`이 같은 사람인가"를 물어야 하고, 잘못 뭉치면 서로 다른 두 사람의 성적이 한 줄로 섞인다. 그 판정 자체를 없앴다.

## 두 가지 실행 모드 — 스스로 판별한다

| 모드 | 언제 | 하는 일 |
|---|---|---|
| **최초 구축** | `js/teacher/`가 없다 | 0~6단계 전부 |
| **갱신** | 이미 만들어져 있다 | 1·5단계는 **검증만**, 2~4·6단계 실행 |

`--rebuild`면 있어도 최초 구축으로, `--refresh`면 없어도 갱신으로(1단계 실패 시 중단). 평소 쓰는 건 **갱신**이다 — 새 결과 파일을 `inbox/`에 넣고 이 명령 한 줄.

`--dry-run`이면 **읽고 계산하고 보고까지 하되 파일을 쓰지 않는다.**

---

## 0단계 — 사전 점검 + 되돌림 지점

```bash
cd quiz-game && echo "── 모드 판별 ──" && \
( [ -d js/teacher ] && echo "js/teacher 있음 → 갱신" || echo "js/teacher 없음 → 최초 구축" ) && \
echo "── 입력 ──" && \
echo "inbox: $(ls -1 data/results/inbox/*.json 2>/dev/null | wc -l)개" && \
ls -la data/results/ 2>/dev/null || echo "(data/results 없음 — 2단계가 만든다)" ; \
echo "── 되돌림 지점 ──" && \
R=/tmp/teacher-mode-rollback/$(date +%Y%m%d-%H%M%S) && mkdir -p "$R" && \
cp -v js/game.js js/main.js index.html "$R"/ 2>/dev/null ; \
cp -rv js/teacher data/results "$R"/ 2>/dev/null ; \
echo "$R" > /tmp/teacher-mode-rollback/LATEST && echo "되돌림: $R"
```

**되돌림 지점을 맨 앞에 잡는 이유.** 1단계는 `game.js`·`main.js`·`index.html`을 고친다. 그 뒤 검증이 깨졌을 때 되돌릴 자리가 없으면 **게임 자체가 망가진 채 남는다.** 6단계의 백업은 "검증을 통과한 상태를 보관"하는 것이라 목적이 다르다 — 망가진 뒤에 뜨는 백업은 구제책이 못 된다.

**검증:** 되돌림 폴더에 `game.js`·`main.js`·`index.html`이 복사됐는가. 하나라도 빠지면 **0단계 실패로 중단.**

**입력이 비어 있으면 여기서 멈춘다.** `inbox/`가 비었는데 `results.json`도 없으면 낼 통계가 없다. "결과 파일을 `quiz-game/data/results/inbox/`에 넣으세요"를 안내하고, **최초 구축이면 1단계까지만 하고 멈춘다**(반출 통로를 먼저 만들어야 파일을 낼 수 있으므로).

---

## 1단계 — 반출 통로 (`/teacher-export`)

`.claude/commands/teacher-export.md`의 절차를 그대로 따른다. 요지:

- `js/game.js`에 문항별 응답 로그(`responses`)와 소요시간을 더해 **기록 스키마 v2**로 올린다
- `js/teacher/export.js`를 만들고 랭킹 화면에 내보내기·복사 버튼을 단다
- **`views.js`는 건드리지 않는다**

**갱신 모드면 만들지 않고 검증만 한다.** 이미 v2인데 다시 손대면 응답 로그가 두 번 쌓인다.

**검증:** `teacher-export.md` 5절의 검사 전 항목 통과. 실패하면 **되돌린 뒤 중단.**

```bash
R=$(cat /tmp/teacher-mode-rollback/LATEST) && cd quiz-game && \
cp -v "$R"/game.js "$R"/main.js js/ && cp -v "$R"/index.html . && echo "복구 완료"
```

---

## 2단계 — 수집 (`/teacher-collect`)

```bash
cd quiz-game && python3 tools/results_collect.py --dry-run && \
python3 tools/results_collect.py
```

`--dry-run`으로 무엇이 들어오고 무엇이 버려지는지 먼저 본 뒤 반영한다.

**검증:** `teacher-collect.md` 4절 검사 통과 — 중복 판(`name+playedAt`) 0, **참가자 수 = 판 수**, 라벨 유일, 라벨 규칙(1회는 그대로·2회 이상은 `playedAt` 순 `_1`..) 일치, 수치 범위 정상, v1/v2 구분 보존.

**실패하면** `data/results/archive/`의 직전 `results.json`으로 되돌리고 중단.

---

## 3단계 — 통계 (`/teacher-stats`)

```bash
cd quiz-game && python3 tools/results_stats.py
```

**검증:** `teacher-stats.md` 4절 — 평균·중앙값·모표준편차·최고/최저를 **검사 쪽에서 독립적으로 다시 계산해 대조**하고, 석차 규칙(동점 동순위·건너뛰기)과 z·백분위 정의가 맞는지 본다. 스크립트가 낸 수를 형식만 보고 넘기면 검증이 아니다.

**실패하면** `analysis.json`을 지우고 중단(반쯤 맞는 분석 파일이 남는 것이 제일 나쁘다).

---

## 4단계 — 비교·문항 분석 (`/teacher-compare`)

```bash
cd quiz-game && python3 tools/results_compare.py --items
```

**검증:** `teacher-compare.md` 5절 — 3단계 결과(`overall`/`participants`/`categories`/`difficulties`)가 살아 있는지 먼저 본다. `compare`가 `stats`를 덮어쓰는 것이 이 사슬에서 가장 나기 쉬운 사고다. 이어서 노출 수 재계산 일치, 선택지 합 = 노출 수, 표본 미달 문항의 `p`가 `null`인지 확인.

---

## 5단계 — 대시보드 (`/teacher-dashboard`)

`teacher.html` + `css/teacher.css` + `js/teacher/dashboard-*.js`. 갱신 모드면 만들지 않고 검증만 한다 — **화면은 데이터가 바뀌어도 다시 만들 필요가 없다**(계산을 안 하니까).

**검증:** `teacher-dashboard.md` 5절 — 뷰가 통계를 다시 구하지 않는지, `innerHTML`을 안 쓰는지, `file://` 안내가 module이 아닌지, **걷어낸 것(등급·인쇄·학급·성장)이 다시 들어오지 않았는지**, **게임 쪽 네 파일이 대시보드를 모르는지.**

```bash
cd quiz-game && NO_OPEN=1 ./run.sh & sleep 2 && \
for p in teacher.html data/results/analysis.json index.html; do \
  curl -s -o /dev/null -w "$p %{http_code}\n" "http://127.0.0.1:8000/$p"; done
```

셋 다 200이어야 한다. `index.html`을 함께 확인하는 이유는 **선생님 모드를 붙이다 게임을 깨뜨리지 않았는지** 보기 위해서다.

---

## 6단계 — 백업

```bash
cd quiz-game && mkdir -p data/results/archive && T=$(date +%Y%m%d-%H%M%S) && \
cp -v data/results/results.json data/results/archive/results-$T.json && \
cp -v data/results/analysis.json data/results/archive/analysis-$T.json && \
ls -1t data/results/archive/ | grep -E '^(results|analysis)-' | tail -n +61 | \
  while read f; do rm -f "data/results/archive/$f"; done ; \
echo "보관: $(ls -1 data/results/archive/ | wc -l)개" && \
mv -v data/results/inbox/*.json data/results/archive/ 2>/dev/null || echo "(inbox 비어 있음)"
```

**반영이 끝난 `inbox/` 파일은 `archive/`로 옮긴다.** 남겨 두면 다음 실행에서 같은 파일을 또 읽는다 — 중복 제거가 잡아 주긴 하지만, 매번 "추가 0건"만 늘어나 실제로 새로 들어온 것이 무엇인지 안 보인다.

스냅숏은 최근 60개만 남긴다(`results`+`analysis` 쌍이라 실질 30회분 — `tools/backup_bank.py`가 30개를 남기는 것과 같은 셈).

> `data/results/`는 `.gitignore`로 통째로 git에서 제외돼 있다. **이름과 성적이 리포지토리에 들어가면 되돌리기 어렵다.**

---

## 7단계 — 보고

이 순서로 낸다. **숫자보다 그 숫자를 믿어도 되는 범위를 먼저 말한다.**

1. **실행 모드와 다룬 데이터** — 최초 구축/갱신 · 새 파일 N개 · 추가 M판 · 중복 K판 · 버린 것과 이유
2. **표본 경고** — 참가 3명 미만이면 z·백분위 없음, v1 기록이 섞였으면 문항 분석에서 빠진 판 수, 판정 가능 문항 수 / 출제된 문항 수
3. **전체 요약** — 참가 N명(서로 다른 이름 M개) · 평균 μ · 표준편차 σ · 중앙값 · 범위. **`김철민_2`처럼 같은 이름이 여러 번 들어간 경우 그 사실을 한 줄로 밝힌다**
4. **순위표 상위 10** — 석차·라벨·점수·정답률·z·백분위
5. **분야별 평균** 4줄 + 가장 약한 분야
6. **난이도 검증** — 최하→최상 정답률이 계단인가. **역전이 있으면 크게 알린다**
7. **눈에 띄는 참가자** — 이상치, 그리고 **정답률은 높은데 점수가 낮은 경우**(쉬운 문제만 맞힌 것이다. 총점만 봐서는 안 보인다)
8. **재검토 후보 문항** — 🔴 먼저, 근거 수치와 함께
9. **다음에 할 일** — 예:
   ```
   /quiz-validate 과학       ← 후보가 몰린 분야의 내용 검증
   /quiz-daily 4             ← 약한 분야 문항 보강
   ```
10. **대시보드 주소**와 브라우저 확인 체크리스트

---

## 실패했을 때

| 단계 | 되돌리는 것 |
|---|---|
| 0 | 없음 (아직 아무것도 안 고쳤다) |
| 1 | `/tmp/teacher-mode-rollback/<타임스탬프>/`의 `game.js`·`main.js`·`index.html` |
| 2 | `data/results/archive/`의 직전 `results.json` |
| 3·4 | `analysis.json` 삭제 후 3단계부터 |
| 5 | `teacher.html`·`css/teacher.css`·`js/teacher/dashboard-*.js` 삭제 (데이터는 멀쩡하다) |

**어느 단계에서 멈춰도 게임(`index.html`)은 정상이어야 한다.** 선생님 모드가 게임을 인질로 잡으면 안 된다. 중단할 때 이걸 마지막으로 확인한다.

```bash
cd quiz-game && NO_OPEN=1 ./run.sh & sleep 2 && \
curl -s -o /dev/null -w "게임 %{http_code}\n" http://127.0.0.1:8000/index.html && \
python3 tools/validate_bank.py | tail -3
```

---

## 평소 쓰는 법

```bash
# 1. 각자 랭킹 화면에서 "결과 내보내기"를 눌러 파일을 낸다
# 2. 받은 파일을 전부 여기에 넣는다
#    quiz-game/data/results/inbox/
# 3. 한 줄
/teacher-mode
```

이름을 가리려면 `--anon`.

## 결과 파일 없이 시험만 해 보려면

브라우저가 없는 환경에서 파이프라인만 확인할 때 쓴다.

```bash
cd quiz-game
python3 tools/make_test_results.py              # 현실 규모 17판 (골칫거리를 다 심어 둔다)
python3 tools/make_test_results.py --bulk 400   # 문항 분석이 돌 만큼 노출을 쌓는다
```

넣고 나서 `/teacher-mode`를 돌리면 된다. 가짜 데이터는 `testData: true`를 달고
파일명이 `TEST-`로 시작한다 — 실제 결과와 섞지 말 것.
