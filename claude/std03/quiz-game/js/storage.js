// [모듈1] 데이터 계층 — 문제 샘플링 + 랭킹 영속화
// UI/사운드에 대한 의존이 전혀 없다. 노드나 콘솔에서 단독 실행·검증 가능.

import {
  QUESTION_BANK,
  CATEGORY_ORDER,
  DIFFICULTY_ORDER,
} from '../data/questions.js';

/** localStorage 키. 다른 프로젝트와 겹치지 않도록 quiz. 접두어를 쓴다. */
const RANKINGS_KEY = 'quiz.rankings';
/** 바로 앞 게임에서 출제한 문항 id. 다음 판에서 되도록 피하는 데 쓴다. */
const LAST_SET_KEY = 'quiz.lastSet';

/**
 * 분야당 출제 문항 수와 난이도 배분.
 * 최상1 / 상2 / 중4 / 하2 / 최하1 = 10문제 → 요청 비율 10:20:40:20:10과 같다.
 * 합이 QUESTIONS_PER_CATEGORY와 일치해야 한다.
 */
export const QUESTIONS_PER_CATEGORY = 10;
export const TOTAL_QUESTIONS = QUESTIONS_PER_CATEGORY * CATEGORY_ORDER.length;
const DIFFICULTY_QUOTA = { top: 1, high: 2, mid: 4, low: 2, bottom: 1 };

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
 * DIFFICULTY_ORDER가 bottom→top 순으로 정렬돼 있어 인덱스 차이가 곧 난이도 거리다.
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

/**
 * 한 분야의 문제 풀에서 난이도 비율을 지켜 QUESTIONS_PER_CATEGORY개를 뽑는다.
 *
 * exclude에 든 id(=직전 판에 나온 문항)는 **난이도 안에서 뒤로 미룰 뿐 제외하지는 않는다.**
 * 난이도별로 "안 나온 것 먼저, 모자라면 나온 것"으로 이어 붙여 두고 앞에서부터 잘라 쓰므로,
 * 어떤 난이도의 여유분이 바닥나도 **할당량과 비율은 그대로 지켜진다**(중복 회피는 최선 노력).
 */
function pickFromCategory(pool, exclude) {
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
    const bucket = byDifficulty[difficulty];
    const fresh = bucket.filter((q) => !exclude.has(q.id));
    const used = bucket.filter((q) => exclude.has(q.id));
    byDifficulty[difficulty] = [...shuffle(fresh), ...shuffle(used)];
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
 *
 * 분야별 10문제씩 난이도 비율을 지켜 뽑은 뒤 **40문제 전체를 다시 섞어서** 돌려준다.
 * 그래서 분야가 뒤섞여 나오고, 다음에 어떤 분야가 나올지 예측되지 않는다.
 * (분야별로 몇 문제가 나가는지는 그대로다 — 섞는 것은 순서뿐이다.)
 *
 * @param {{exclude?: Iterable<string>}} [options] exclude에 든 id는 되도록 피한다.
 */
export function getQuizSet(options = {}) {
  const exclude =
    options.exclude instanceof Set ? options.exclude : new Set(options.exclude || []);

  const quizSet = [];
  CATEGORY_ORDER.forEach((category) => {
    const pool = QUESTION_BANK.filter((q) => q.category === category);
    quizSet.push(...pickFromCategory(pool, exclude));
  });
  return shuffle(quizSet);
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

  /**
   * 방금 끝난 판의 문항 id를 남긴다. 다음 판에서 되도록 피하려는 용도라
   * **저장에 실패해도 조용히 넘어간다**(중복이 좀 생길 뿐 게임에는 지장이 없다).
   * 새로고침으로 페이지를 다시 열어도 이어지도록 localStorage에 둔다.
   */
  saveLastQuestionIds(ids) {
    try {
      localStorage.setItem(LAST_SET_KEY, JSON.stringify(Array.from(ids)));
      return true;
    } catch (error) {
      return false;
    }
  },

  /** 직전 판의 문항 id 목록. 없거나 깨져 있으면 빈 배열. */
  getLastQuestionIds() {
    try {
      const parsed = JSON.parse(localStorage.getItem(LAST_SET_KEY) || '[]');
      return Array.isArray(parsed) ? parsed.filter((id) => typeof id === 'string') : [];
    } catch (error) {
      return [];
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
//   // 2) 난이도 비율(분야당 top1/high2/mid4/low2/bottom1) 확인
//   const byDiff = {};
//   set.forEach(q => byDiff[q.difficulty] = (byDiff[q.difficulty]||0) + 1);
//   console.log(byDiff);         // { top:4, high:8, mid:16, low:8, bottom:4 }
//
//   // 3) 출제 순서가 분야를 가로질러 섞였는지 확인
//   console.log(set.map(q => q.category.slice(0,2)).join(' '));  // 뒤섞여 보여야 정상
//
//   // 4) 직전 판 회피 확인 — 겹치는 문항이 0개여야 한다
//   const again = getQuizSet({ exclude: set.map(q => q.id) });
//   const prev = new Set(set.map(q => q.id));
//   console.log(again.filter(q => prev.has(q.id)).length);       // 0
//
//   // 4) 저장 → 정렬 조회 확인
//   Storage.clearRankings();
//   Storage.saveRecord({ name:'가', score:120, correctCount:22, total:40,
//     accuracy:0.55, byCategory:{}, playedAt:new Date().toISOString() });
//   Storage.saveRecord({ name:'나', score:160, correctCount:30, total:40,
//     accuracy:0.75, byCategory:{}, playedAt:new Date().toISOString() });
//   console.log(Storage.getRankings().map(r => r.name));        // ['나', '가']
