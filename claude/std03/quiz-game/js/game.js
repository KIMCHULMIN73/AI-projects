// [모듈3] 게임 상태머신 — 모듈1(데이터)과 모듈2(뷰)를 연결한다.
// 모듈1/2의 인터페이스만 사용하고 내부는 건드리지 않는다.
//
// 상태 흐름: START → PLAYING → FEEDBACK → (반복) → RESULT → RANKING

import { getQuizSet, Storage } from './storage.js';
import { CATEGORY_ORDER } from '../data/questions.js';
import { Views } from './views.js';
import { Audio } from './audio.js';

const INITIAL_SCORE = 100;
const CORRECT_DELTA = 10;
const WRONG_DELTA = -10;

const state = {
  phase: 'START', // START | PLAYING | FEEDBACK | RESULT | RANKING
  name: '',
  questions: [],
  index: 0,
  score: INITIAL_SCORE,
  /** category → { correct, total } */
  tally: {},
  /** 배경·BGM은 분야가 바뀔 때만 전환한다(문제마다 트랙을 갈면 곡이 계속 끊긴다). */
  lastCategory: null,
};

function resetTally() {
  state.tally = {};
  CATEGORY_ORDER.forEach((category) => {
    state.tally[category] = { correct: 0, total: 0 };
  });
}

/** 현재 문제를 화면에 그린다. 분야가 바뀌었으면 배경과 BGM도 전환한다. */
function renderCurrentQuestion() {
  const question = state.questions[state.index];
  if (!question) return;

  if (question.category !== state.lastCategory) {
    state.lastCategory = question.category;
    Views.setBackground(question.category);
    if (state.index > 0) Audio.nextBgm(); // 첫 분야는 startBgm이 이미 재생 중
  }

  Views.showQuestion(question, {
    name: state.name,
    index: state.index,
    total: state.questions.length,
    score: state.score,
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

/** 문제 세트를 새로 뽑아 처음부터 시작한다(이름은 유지). */
function beginRound() {
  state.questions = getQuizSet();
  state.index = 0;
  state.score = INITIAL_SCORE;
  state.lastCategory = null;
  state.phase = 'PLAYING';
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

    const isCorrect = index === question.answerIndex;
    const delta = isCorrect ? CORRECT_DELTA : WRONG_DELTA;
    state.score += delta;

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
      name: summary.name,
      score: summary.score,
      correctCount: summary.correctCount,
      total: summary.total,
      accuracy: summary.accuracy,
      byCategory: summary.byCategory,
      playedAt: new Date().toISOString(),
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
    state.lastCategory = null;
    Audio.stopBgm();
    Views.showStart();
    Views.focusNameInput();
  },

  /** 디버깅용 스냅샷. */
  getState() {
    return { ...state, questions: state.questions.map((q) => q.id) };
  },
};
