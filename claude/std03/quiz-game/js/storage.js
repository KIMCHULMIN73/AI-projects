// [모듈1] 데이터 계층 — 문제 샘플링 + 랭킹 영속화
// UI/사운드에 대한 의존이 전혀 없다. 노드나 콘솔에서 단독 실행·검증 가능.

import {
  QUESTION_BANK,
  CATEGORY_ORDER,
  DIFFICULTY_ORDER,
} from '../data/questions.js';

/** localStorage 키. 다른 프로젝트와 겹치지 않도록 quiz. 접두어를 쓴다. */
const RANKINGS_KEY = 'quiz.rankings';

/** 분야당 출제 문항 수와 난이도 배분(PRD §2: 최상1 / 상2 / 중5 / 하2). */
export const QUESTIONS_PER_CATEGORY = 10;
export const TOTAL_QUESTIONS = QUESTIONS_PER_CATEGORY * CATEGORY_ORDER.length;
const DIFFICULTY_QUOTA = { top: 1, high: 2, mid: 5, low: 2 };

/** Fisher–Yates. 원본을 건드리지 않고 새 배열을 돌려준다. */
function shuffle(list) {
  const out = list.slice();
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

/**
 * 해당 난이도 풀이 비었을 때 보충할 가장 가까운 난이도를 찾는다.
 * DIFFICULTY_ORDER가 low→top 순으로 정렬돼 있어 인덱스 차이가 곧 난이도 거리다.
 */
function nearestAvailable(byDifficulty, target) {
  const targetRank = DIFFICULTY_ORDER.indexOf(target);
  let best = null;
  let bestDistance = Infinity;
  DIFFICULTY_ORDER.forEach((difficulty, rank) => {
    if (byDifficulty[difficulty].length === 0) return;
    const distance = Math.abs(rank - targetRank);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = difficulty;
    }
  });
  return best;
}

/** 한 분야의 문제 풀에서 난이도 비율을 지켜 QUESTIONS_PER_CATEGORY개를 뽑는다. */
function pickFromCategory(pool) {
  const byDifficulty = {};
  DIFFICULTY_ORDER.forEach((difficulty) => {
    byDifficulty[difficulty] = [];
  });
  pool.forEach((question) => {
    if (byDifficulty[question.difficulty]) {
      byDifficulty[question.difficulty].push(question);
    }
  });
  DIFFICULTY_ORDER.forEach((difficulty) => {
    byDifficulty[difficulty] = shuffle(byDifficulty[difficulty]);
  });

  const picked = [];
  const shortfalls = [];

  // 1차: 난이도별 할당량만큼 그대로 뽑는다.
  DIFFICULTY_ORDER.forEach((difficulty) => {
    const need = DIFFICULTY_QUOTA[difficulty] || 0;
    const taken = byDifficulty[difficulty].splice(0, need);
    picked.push(...taken);
    if (taken.length < need) {
      shortfalls.push({ difficulty, missing: need - taken.length });
    }
  });

  // 2차: 모자란 만큼 인접 난이도에서 보충한다(문항이 부족한 분야 대응).
  shortfalls.forEach(({ difficulty, missing }) => {
    for (let i = 0; i < missing; i++) {
      const donor = nearestAvailable(byDifficulty, difficulty);
      if (!donor) return; // 분야 전체가 바닥나면 더 채울 수 없다.
      picked.push(byDifficulty[donor].shift());
    }
  });

  // 분야 안에서는 난이도 순서가 드러나지 않도록 섞는다.
  return shuffle(picked);
}

/**
 * 게임 한 판에 쓸 문제 세트를 만든다.
 * 분야별 10문제씩, 난이도 비율을 지켜 무작위 샘플링하며 중복은 없다.
 * 반환 배열은 CATEGORY_ORDER 순서(한국사→과학→세계지리→예술)로 이어 붙인다.
 */
export function getQuizSet() {
  const quizSet = [];
  CATEGORY_ORDER.forEach((category) => {
    const pool = QUESTION_BANK.filter((q) => q.category === category);
    quizSet.push(...pickFromCategory(pool));
  });
  return quizSet;
}

/** localStorage 원본 읽기. 실패하거나 형식이 깨져 있으면 빈 배열로 취급한다. */
function readRankings() {
  try {
    const raw = localStorage.getItem(RANKINGS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (error) {
    console.warn('[storage] 랭킹을 읽지 못했습니다. 빈 목록으로 진행합니다.', error);
    return [];
  }
}

export const Storage = {
  /**
   * 기록 한 건을 추가한다. 저장에 실패해도 예외를 밖으로 던지지 않는다
   * (게임 진행이 저장 실패로 멈추면 안 되므로).
   * @param {{name, score, correctCount, total, accuracy, byCategory, playedAt}} record
   * @returns {boolean} 저장 성공 여부
   */
  saveRecord(record) {
    try {
      const rankings = readRankings();
      rankings.push(record);
      localStorage.setItem(RANKINGS_KEY, JSON.stringify(rankings));
      return true;
    } catch (error) {
      console.warn('[storage] 랭킹을 저장하지 못했습니다.', error);
      return false;
    }
  },

  /** 점수 내림차순, 동점이면 최신 기록이 먼저 오도록 정렬해 반환한다. */
  getRankings() {
    return readRankings().slice().sort((a, b) => {
      if (b.score !== a.score) return b.score - a.score;
      return new Date(b.playedAt || 0) - new Date(a.playedAt || 0);
    });
  },

  /** 개발/테스트용. 저장된 랭킹을 모두 지운다. */
  clearRankings() {
    try {
      localStorage.removeItem(RANKINGS_KEY);
      return true;
    } catch (error) {
      console.warn('[storage] 랭킹을 지우지 못했습니다.', error);
      return false;
    }
  },
};

// ── 콘솔 테스트 예시 ───────────────────────────────────────────────
// 브라우저 콘솔에서 (index.html을 로컬 서버로 연 상태에서):
//
//   const { getQuizSet, Storage } = await import('./js/storage.js');
//
//   // 1) 40문제 · 분야별 10문제 · 중복 없음 확인
//   const set = getQuizSet();
//   console.log(set.length);                                   // 40
//   console.log(new Set(set.map(q => q.id)).size);              // 40 (중복 없음)
//   const byCat = {};
//   set.forEach(q => byCat[q.category] = (byCat[q.category]||0) + 1);
//   console.log(byCat);          // 각 분야 10
//
//   // 2) 난이도 비율(분야당 top1/high2/mid5/low2) 확인
//   const byDiff = {};
//   set.forEach(q => byDiff[q.difficulty] = (byDiff[q.difficulty]||0) + 1);
//   console.log(byDiff);         // { top:4, high:8, mid:20, low:8 }
//
//   // 3) 재호출 시 문제가 바뀌는지 확인
//   console.log(getQuizSet()[0].id !== getQuizSet()[0].id);     // 대체로 true
//
//   // 4) 저장 → 정렬 조회 확인
//   Storage.clearRankings();
//   Storage.saveRecord({ name:'가', score:120, correctCount:22, total:40,
//     accuracy:0.55, byCategory:{}, playedAt:new Date().toISOString() });
//   Storage.saveRecord({ name:'나', score:160, correctCount:30, total:40,
//     accuracy:0.75, byCategory:{}, playedAt:new Date().toISOString() });
//   console.log(Storage.getRankings().map(r => r.name));        // ['나', '가']
