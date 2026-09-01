// [모듈3] 게임 상태머신 — 모듈1(데이터)과 모듈2(뷰)를 연결한다.
// 모듈1/2의 인터페이스만 사용하고 내부는 건드리지 않는다.
//
// 상태 흐름: START → PLAYING → FEEDBACK → (반복) → RESULT → RANKING

import { getQuizSet, Storage } from './storage.js';
import { CATEGORY_ORDER } from '../data/questions.js';
import { Views } from './views.js';
import { Audio } from './audio.js';

const INITIAL_SCORE = 100;

/**
 * 난이도별 정답 배점. 어려운 문제를 맞힐수록 크게 오른다.
 * **점수 규칙은 모듈3의 소관**이므로 뷰는 이 표를 모른다 — 화면에 띄울 점수는
 * showQuestion에 인자로 넘겨 준다.
 */
const POINTS_BY_DIFFICULTY = { top: 50, high: 40, mid: 30, low: 20, bottom: 10 };
/** 표에 없는 난이도가 들어와도 게임이 멈추지 않도록 하는 기본값. */
const FALLBACK_POINTS = 10;
/** 오답은 난이도와 무관하게 일괄 -10. */
const WRONG_DELTA = -10;

/** 몇 문제마다 BGM을 바꿀지. 40문제 한 판에 4곡이 흐른다. */
const BGM_SWITCH_EVERY = 10;

/** 이 문제를 맞히면 얻는 점수. */
function pointsFor(question) {
  return POINTS_BY_DIFFICULTY[question.difficulty] ?? FALLBACK_POINTS;
}

const state = {
  phase: 'START', // START | PLAYING | FEEDBACK | RESULT | RANKING
  name: '',
  questions: [],
  index: 0,
  score: INITIAL_SCORE,
  /** category → { correct, total } */
  tally: {},
  /** 판이 시작된 시각(ms). 한 판의 총 소요시간을 재는 데 쓴다. */
  startedAt: 0,
  /** 현재 문제가 화면에 뜬 시각(ms). 문항별 소요시간을 재는 데 쓴다. */
  shownAt: 0,
  /**
   * 문항별 응답 로그. **선생님 모드(문항 분석)의 유일한 근거다.**
   * 이게 없으면 "어느 문항에서 무너졌나"를 영원히 알 수 없다.
   * 기록에 실려 나가되, 이 배열을 읽는 쪽이 배점을 다시 계산해선 안 된다
   * (점수 규칙은 모듈3의 소관이다 — delta는 계산 근거가 아니라 기록이다).
   */
  responses: [],
};

function resetTally() {
  state.tally = {};
  CATEGORY_ORDER.forEach((category) => {
    state.tally[category] = { correct: 0, total: 0 };
  });
}

/**
 * 현재 문제를 화면에 그린다.
 *
 * 출제 순서를 섞은 뒤로는 분야가 거의 매 문제 바뀌므로 **배경과 BGM을 떼어 놨다.**
 * - 배경: 문제의 분야를 따라간다. `setBackground`가 같은 키면 스스로 no-op이라
 *   같은 분야가 연달아 나와도 헛일을 하지 않는다.
 * - BGM: 분야가 아니라 **진행 문항 수**를 따라 BGM_SWITCH_EVERY마다 바꾼다.
 *   분야마다 갈아 끼우면 곡이 매번 처음부터 다시 시작해 음악이 성립하지 않는다.
 */
function renderCurrentQuestion() {
  const question = state.questions[state.index];
  if (!question) return;

  state.shownAt = Date.now();
  Views.setBackground(question.category);
  if (state.index > 0 && state.index % BGM_SWITCH_EVERY === 0) {
    Audio.nextBgm(); // 첫 곡은 startBgm이 이미 재생 중
  }

  Views.showQuestion(question, {
    name: state.name,
    index: state.index,
    total: state.questions.length,
    score: state.score,
    points: pointsFor(question),
  });
}

/** 40문제를 다 푼 뒤의 성적 요약. */
function buildSummary() {
  const total = state.questions.length;
  const correctCount = Object.values(state.tally).reduce((sum, t) => sum + t.correct, 0);

  const byCategory = {};
  CATEGORY_ORDER.forEach((category) => {
    const { correct, total: categoryTotal } = state.tally[category];
    byCategory[category] = categoryTotal > 0 ? correct / categoryTotal : 0;
  });

  return {
    name: state.name,
    score: state.score,
    correctCount,
    total,
    accuracy: total > 0 ? correctCount / total : 0,
    byCategory,
  };
}

/**
 * 문제 세트를 새로 뽑아 처음부터 시작한다(이름은 유지).
 * 직전 판에 나온 문항은 되도록 피하고, 이번 판의 문항 id를 다음 판을 위해 남긴다.
 */
function beginRound() {
  state.questions = getQuizSet({ exclude: Storage.getLastQuestionIds() });
  Storage.saveLastQuestionIds(state.questions.map((q) => q.id));

  state.index = 0;
  state.score = INITIAL_SCORE;
  state.phase = 'PLAYING';
  state.startedAt = Date.now();
  state.responses = [];
  resetTally();
  renderCurrentQuestion();
}

export const Game = {
  /** 시작 화면에서 이름을 받아 게임을 연다. */
  start(name) {
    const trimmed = String(name || '').trim();
    if (!trimmed) return false;

    state.name = trimmed;
    Audio.startBgm(); // 사용자 클릭 직후라 자동재생 정책에 걸리지 않는다
    beginRound();
    return true;
  },

  /** 보기를 골랐을 때. PLAYING 상태에서만 반응한다(중복 클릭 방어). */
  answer(choiceIndex) {
    if (state.phase !== 'PLAYING') return;
    const question = state.questions[state.index];
    if (!question) return;

    const index = Number(choiceIndex);
    if (!Number.isInteger(index) || index < 0 || index >= question.choices.length) return;

    state.phase = 'FEEDBACK';

    // 정답은 난이도만큼(최상 +50 … 최하 +10), 오답은 언제나 -10.
    const isCorrect = index === question.answerIndex;
    const delta = isCorrect ? pointsFor(question) : WRONG_DELTA;
    state.score += delta;

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

    const tally = state.tally[question.category];
    if (tally) {
      tally.total += 1;
      if (isCorrect) tally.correct += 1;
    }

    if (isCorrect) Audio.playCorrect();
    else Audio.playWrong();

    Views.showFeedback(isCorrect, question.explanation, {
      delta,
      selectedIndex: index,
      answerIndex: question.answerIndex,
    });
  },

  /** 피드백 팝업의 "다음 문제". 마지막 문제였다면 완료 화면으로. */
  next() {
    if (state.phase !== 'FEEDBACK') return;

    state.index += 1;
    if (state.index >= state.questions.length) {
      state.phase = 'RESULT';
      Views.hideFeedback();
      Views.showResult(buildSummary());
      return;
    }

    state.phase = 'PLAYING';
    Views.hideFeedback();
    renderCurrentQuestion(); // 새 점수를 먼저 그린 뒤에
    Views.bumpScore();       // 그 숫자를 튀어오르게 한다
  },

  /** 완료 화면의 "다시 도전" — 같은 이름으로 문제를 새로 뽑아 100점부터. */
  retry() {
    if (state.phase !== 'RESULT') return;
    beginRound();
  },

  /** 완료 화면의 "종료" — 기록을 저장하고 랭킹으로. */
  finish() {
    if (state.phase !== 'RESULT') return;
    state.phase = 'RANKING';

    const summary = buildSummary();
    const record = {
      // 스키마 표기가 있어야 뒤에 읽는 쪽이 구버전 기록과 구분할 수 있다.
      // 이 표기가 없는 기록 = responses가 없는 기록이고, 그건 문항 분석에서
      // 빠질 뿐 총점 통계에는 그대로 들어간다.
      schema: 'quiz-record/v2',
      name: summary.name,
      score: summary.score,
      correctCount: summary.correctCount,
      total: summary.total,
      accuracy: summary.accuracy,
      byCategory: summary.byCategory,
      playedAt: new Date().toISOString(),
      durationMs: Date.now() - state.startedAt,
      // 다음 판이 beginRound에서 새 배열을 넣으므로 참조를 그대로 둬도 되지만,
      // 기록이 나중에 바뀌지 않는다는 것을 코드로 못박아 둔다.
      responses: state.responses.slice(),
    };

    const saved = Storage.saveRecord(record);
    let rankings = Storage.getRankings();

    // 저장이 실패해도(사생활 보호 모드 등) 이번 판 성적은 보여 준다.
    if (!saved) {
      rankings = [...rankings, record].sort((a, b) => {
        if (b.score !== a.score) return b.score - a.score;
        return new Date(b.playedAt || 0) - new Date(a.playedAt || 0);
      });
    }

    const highlightIndex = rankings.findIndex(
      (item) => item.playedAt === record.playedAt && item.name === record.name
    );

    Audio.stopBgm();
    Views.showRanking(rankings, { highlightIndex });
  },

  /** 랭킹 화면의 "다시 시작하기" — 시작 화면으로 되돌린다. */
  restart() {
    state.phase = 'START';
    Audio.stopBgm();
    Views.showStart();
    Views.focusNameInput();
  },

  /** 디버깅용 스냅샷. */
  getState() {
    return { ...state, questions: state.questions.map((q) => q.id) };
  },
};
