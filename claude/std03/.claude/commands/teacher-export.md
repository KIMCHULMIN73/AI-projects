---
description: 학생 브라우저에 갇힌 성적을 파일로 꺼내는 반출 통로를 만든다 (선생님 모드 1단계)
argument-hint: "[--verify 만 확인 | --rebuild 다시 만들기 (비우면 없으면 만들고 있으면 검증)]"
allowed-tools: Bash, Read, Grep, Write, Edit
---

실행 인수: **$ARGUMENTS**

> 이 하네스는 위치 인수(`$1`)를 신뢰할 수 없다 — 0-based로 밀려 들어온다.
> **`$ARGUMENTS` 한 덩어리만 쓰고 옵션은 그 문자열에서 골라낼 것.**

선생님 모드의 **첫 관문**이다. 나머지 네 단계는 전부 이 단계가 뱉는 파일을 먹고 산다.

## 이 단계가 푸는 문제

성적은 `localStorage['quiz.rankings']`에 있다. **그 브라우저 안에서만 존재한다.** 여러 사람이 각자 자기 기기에서 풀면 성적도 그만큼 흩어진 채 서로를 모른다. 한 화면에서 비교하려면 **먼저 꺼내야 한다.**

동시에 두 번째 문제가 있다. 지금 기록은 이렇게 생겼다.

```js
{ name, score, correctCount, total, accuracy, byCategory, playedAt }
```

**어떤 문제를 틀렸는지가 없다.** 이대로는 "어느 문항에서 무너졌나", "이 문항 변별도가 얼마인가"를 영원히 알 수 없다. 총점만 비교하는 선생님 모드는 만들 값어치가 없다. 그래서 이 단계에서 **기록 스키마를 v2로 올린다.**

## 없으면 만들고, 있으면 검증한다

이 명령은 여러 번 돌려도 안전해야 한다. 먼저 상태를 본다.

```bash
cd quiz-game && echo "── 반출 모듈 ──" && ls -la js/teacher/ 2>/dev/null || echo "(없음 — 새로 만든다)" ; \
echo "── 게임 쪽 개입 지점 ──" && grep -n "responses\|exportBtn\|durationMs\|quiz-record" js/game.js js/main.js index.html 2>/dev/null || echo "(아직 손대지 않음)"
```

- `js/teacher/export.js`가 있고 아래 계약을 다 만족하면 → **검증만 하고 끝낸다.**
- 없거나 계약이 깨졌으면 → 만든다. `--rebuild`면 있어도 다시 만든다.
- `--verify`면 만들지 않고 검증만 한다(실패해도 파일을 건드리지 않는다).

---

## 1. 기록 스키마 v2 — 문항별 응답을 남긴다

`js/game.js`를 고친다. 손대는 곳은 셋뿐이다.

> 1단계가 건드리는 파일은 넷이다 — `js/game.js`(응답 로그), `js/storage.js`(읽기 하나 추가), `index.html`(버튼), `js/main.js`(배선). CSS에 `.btn-ghost`가 없으면 `css/style.css`에도 최소한으로 더한다. **`js/views.js`는 절대 건드리지 않는다.**

**(1) 판이 시작될 때** — `beginRound()` 안에서 `state.startedAt = Date.now()`, `state.responses = []`.

**(2) 문제를 그릴 때** — `renderCurrentQuestion()` 안에서 `state.shownAt = Date.now()`. 문항별 소요시간을 재기 위한 것이다.

**(3) 답을 고를 때** — `answer()`가 점수를 계산한 직후 한 줄 남긴다.

```js
state.responses.push({
  id: question.id,
  category: question.category,
  difficulty: question.difficulty,
  selectedIndex: index,
  answerIndex: question.answerIndex,
  correct: isCorrect,
  delta,
  elapsedMs: Date.now() - state.shownAt,
});
```

그리고 `finish()`가 만드는 `record`에 `responses: state.responses`, `durationMs: Date.now() - state.startedAt`, `schema: 'quiz-record/v2'`를 더한다.

**지켜야 할 경계 두 가지.**

- **점수 규칙은 여전히 모듈3의 소관이다.** `delta`를 로그에 남기는 것은 기록일 뿐, 뒤 단계가 `POINTS_BY_DIFFICULTY`를 다시 계산하게 만들지 말 것.
- **뷰는 이 변경을 전혀 몰라야 한다.** `views.js`는 한 줄도 고치지 않는다.

**호환성 — 이게 제일 중요하다.** 브라우저에는 **v2 이전에 쌓인 기록이 이미 들어 있다.** `responses`가 없는 기록이다. 버리면 안 되고, 있는 척해서도 안 된다. 뒤 단계 전부가 이 규칙을 따른다.

| 기록 | `responses` | 총점·정답률 통계 | 문항별 분석 |
|---|---|---|---|
| v2 | 있음 | 포함 | 포함 |
| v1 (구버전) | 없음 → `null` | **포함** | **제외** |

`responses`가 없는 것과 `responses: []`(한 문제도 안 푼 판)는 **다른 것**이다. 빈 배열로 뭉개면 v1 기록이 "전부 오답"으로 계산되어 평균이 통째로 거짓말을 한다. 반드시 `null`로 구분할 것.

**검증:**

```bash
cd quiz-game && python3 - <<'PY'
import re, sys
src = open('js/game.js', encoding='utf-8').read()
need = [('응답 로그 push',    r'state\.responses\.push'),
        ('문제 표시 시각',    r'state\.shownAt'),
        ('판 시작 시각',      r'state\.startedAt'),
        ('record에 responses', r'responses:\s*state\.responses'),
        ('record에 durationMs', r'durationMs'),
        ('스키마 표기',        r'quiz-record/v2')]
bad = [n for n, p in need if not re.search(p, src)]
print('game.js v2:', 'PASS' if not bad else 'FAIL ' + ', '.join(bad))
v = open('js/views.js', encoding='utf-8').read()
print('views.js 무개입:', 'PASS' if 'responses' not in v else 'FAIL (뷰가 응답 로그를 안다)')
sys.exit(1 if bad else 0)
PY
```

---

## 2. 반출 파일 형식 — `quiz-export/v2`

**이 스키마가 선생님 모드 전체의 계약이다.** 뒤 네 단계가 전부 이걸 읽는다. 바꾸려면 `/teacher-collect`의 파서도 같이 고쳐야 한다.

```json
{
  "schema": "quiz-export/v2",
  "exportedAt": "2026-09-01T12:00:00.000Z",
  "exportedBy": "김철민",
  "app": "std03-quiz",
  "records": [
    {
      "schema": "quiz-record/v2",
      "name": "김철민",
      "score": 340,
      "correctCount": 28,
      "total": 40,
      "accuracy": 0.7,
      "byCategory": { "korean_history": 0.8, "science": 0.6,
                      "world_geography": 0.7, "arts_culture": 0.7 },
      "playedAt": "2026-09-01T11:50:00.000Z",
      "durationMs": 512340,
      "responses": [
        { "id": "KH-012", "category": "korean_history", "difficulty": "mid",
          "selectedIndex": 2, "answerIndex": 2, "correct": true,
          "delta": 30, "elapsedMs": 7120 }
      ]
    }
  ]
}
```

- `exportedBy`는 **이 파일을 낸 사람**, `records[].name`은 **그 판을 친 사람**이다. 한 기기를 돌려 쓰면 갈린다 — 그래서 따로 둔다. 통계는 `records[].name`만 쓴다.
- `playedAt`은 **기록의 신원이다.** 중복 판정(`name + playedAt`)이 여기 걸려 있으므로 반출할 때 절대 다시 찍지 말 것. `exportedAt`과 헷갈리면 같은 판이 몇 번이고 다시 들어온다.

---

## 3. `js/teacher/export.js` — 반출 모듈

`js/teacher/`를 새로 만들고 그 아래 둔다. **게임 코드가 이 파일을 import 하지 않는다** — 반대로 이 파일이 `storage.js`를 읽는다. 지워도 게임은 그대로 돈다(`tools/`·`preview.html`과 같은 규율).

| 함수 | 하는 일 |
|---|---|
| `buildExport(name?)` | `localStorage`에서 기록을 읽어 위 스키마의 객체를 만든다. 이름을 주면 그 사람 기록만 |
| `downloadExport(name?)` | `quiz-result-<이름>-<YYYYMMDD-HHmm>.json`으로 내려받게 한다 |
| `copyExport(name?)` | 같은 내용을 클립보드로. 다운로드가 막힌 환경의 대비책 |

구현 주의:

- `Storage.getRankings()`를 쓰지 말고 **원본 순서 그대로** 읽는다. 랭킹 정렬은 화면용이고 반출은 있는 그대로여야 한다.
  - 그래서 **모듈1(`js/storage.js`)에 `Storage.getAllRecords()`를 하나 더한다** — 저장된 순서 그대로를 돌려주는 읽기다. 기존 동작을 바꾸지 않는 추가다.
  - **반출 모듈이 `localStorage`를 직접 열게 하지 말 것.** 그러면 `'quiz.rankings'` 키가 두 파일에 살게 되어, 키를 바꾸는 날 한쪽만 고치게 된다. localStorage 접근은 모듈1 안에 묶어 둔다.
- 파일명의 이름에서 **경로 문자를 걷어낸다**(`/ \ : * ? " < > |`와 제어문자 → `_`). 이름에 `../`를 넣을 수 있다.
- `Blob` + `URL.createObjectURL` + `<a download>`. **`revokeObjectURL`을 반드시 부를 것.**
- 클립보드가 없거나 거부되면 조용히 삼키지 말고 **화면에 알린다.** 반출 실패를 모르고 넘어가면 그 사람 성적이 통째로 사라진다. (게임 쪽 `Audio`·`saveLastQuestionIds`가 실패를 삼키는 것과 정반대다 — 저쪽은 삼켜도 게임이 돌지만 이건 데이터가 없어진다.)

---

## 4. 랭킹 화면에 버튼 달기

`index.html`의 `#screen-ranking` 안 `.button-row`에 `#restartBtn` 옆으로 둘을 더한다.

```html
<button class="btn btn-ghost" type="button" id="exportBtn">📤 결과 내보내기</button>
<button class="btn btn-ghost" type="button" id="copyBtn">📋 결과 복사</button>
```

`js/main.js`의 `bind()`에서 잇는다. **`main.js`가 배선을 독점한다**는 규율을 지킬 것 — `export.js`가 스스로 `addEventListener`를 부르면 안 된다.

```js
import { TeacherExport } from './teacher/export.js';
$('#exportBtn').addEventListener('click', () => TeacherExport.downloadExport());
$('#copyBtn').addEventListener('click', () => TeacherExport.copyExport());
```

버튼 클래스는 CSS에 이미 있는 것만 쓴다. `.btn-ghost`가 없으면 `css/style.css`에 최소한으로 더하되 **다른 화면의 버튼 모양을 바꾸지 말 것.**

---

## 5. 검증

```bash
cd quiz-game && python3 - <<'PY'
import re, sys, os
ok = True
def chk(l, c, h=''):
    global ok
    print(('  ✅ ' if c else '  ❌ ') + l + ('' if c else '  ← ' + h)); ok = ok and c

print('── 파일 ──')
chk('js/teacher/export.js 존재', os.path.exists('js/teacher/export.js'))
src = open('js/teacher/export.js', encoding='utf-8').read() if os.path.exists('js/teacher/export.js') else ''
for fn in ('buildExport', 'downloadExport', 'copyExport'):
    chk(f'{fn} export', re.search(r'\b' + fn + r'\b', src) is not None)
chk('스키마 표기 quiz-export/v2', 'quiz-export/v2' in src)
chk('objectURL 회수', 'revokeObjectURL' in src, 'URL이 샌다')
chk('파일명 새니타이즈', re.search(r'replace\(', src) is not None, '이름의 경로 문자를 걷어낼 것')

print('── 배선 ──')
html = open('index.html', encoding='utf-8').read()
main = open('js/main.js', encoding='utf-8').read()
for i in ('exportBtn', 'copyBtn'):
    chk(f'#{i} 마크업', f'id="{i}"' in html)
    chk(f'#{i} 바인딩', i in main)
chk('export.js는 스스로 바인딩하지 않는다', 'addEventListener' not in src,
    '배선은 main.js가 독점한다')

print('── 게임 로직 ──')
g = open('js/game.js', encoding='utf-8').read()
chk('응답 로그 기록', 'state.responses.push' in g)
chk('record에 responses 실림', re.search(r'responses:\s*state\.responses', g) is not None)
chk('뷰는 모른다', 'responses' not in open('js/views.js', encoding='utf-8').read())
# 실제 호출만 본다 — 주석에 'localStorage에 갇힌'이라고 적은 것까지 걸리면 곤란하다.
chk('localStorage 접근이 모듈1에 묶여 있다',
    'getAllRecords' in open('js/storage.js', encoding='utf-8').read()
    and not re.search(r'localStorage\s*\.', src),
    "반출 모듈이 'quiz.rankings' 키를 제 손으로 알면 키가 두 곳에 산다")
sys.exit(0 if ok else 1)
PY
```

**전 항목 통과해야 다음 단계로 간다.** 하나라도 ❌면 원인을 보고하고 멈춘다.

브라우저에서 눈으로 확인할 것(이 환경에서는 자동화되지 않는다):

- [ ] 한 판 끝내고 "종료" → 랭킹 화면에 내보내기·복사 버튼이 보인다
- [ ] 내보내기를 누르면 `quiz-result-<이름>-<날짜>.json`이 받아진다
- [ ] 그 파일의 `responses`에 **40개** 항목이 있고 `correct`가 섞여 있다
- [ ] 콘솔에 에러가 없다

---

## 다음

받은 파일들을 `quiz-game/data/results/inbox/`에 모아 두고 **`/teacher-collect`**로 넘긴다.
