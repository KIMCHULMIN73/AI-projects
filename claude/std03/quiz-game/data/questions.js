// ── 문제 은행 진입점 ──────────────────────────────────────────────
//
// 실제 문항은 분야별로 questions/ 아래에 나뉘어 있고, 이 파일은 그것을 합쳐
// 모듈1의 공개 인터페이스(CATEGORIES / CATEGORY_ORDER / DIFFICULTY_ORDER /
// QUESTION_BANK)로 내보내는 역할만 한다. 이 파일을 import 하는 쪽(storage.js)은
// 문항이 몇 개 파일에 나뉘어 있는지 알 필요가 없다.
//
// ── 새 문항 추가 방법 ─────────────────────────────────────────────
// 1. 해당 분야 파일(questions/<분야>.js)의 배열에 객체를 추가한다.
// 2. id는 분야 접두어(KH/SC/WG/AC) + 일련번호, 전체에서 중복 금지.
// 3. choices는 반드시 4개, answerIndex는 0~3(정답 보기의 위치).
// 4. difficulty는 top | high | mid | low | bottom 중 하나, explanation은 필수.
// 5. 분야당 최소 top1/high2/mid4/low2/bottom1은 유지해야 샘플링이 비율을 지킨다.
//    (지금은 분야마다 10/20/40/20/10을 갖춰 두어 매 게임 문제가 충분히 바뀐다.)
// 6. 추가 후 반드시 `python3 tools/validate_bank.py`를 돌릴 것. 다만 검증기는
//    스키마와 분포만 보고 내용의 옳고 그름은 보지 못하므로, CLAUDE.md의
//    「퀴즈 문제 교차 검증 가이드라인」 4가지는 사람이 직접 확인해야 한다.
// ──────────────────────────────────────────────────────────────────

import { KOREAN_HISTORY } from './questions/korean-history.js';
import { SCIENCE } from './questions/science.js';
import { WORLD_GEOGRAPHY } from './questions/world-geography.js';
import { ARTS_CULTURE } from './questions/arts-culture.js';

/** 분야 메타: 표기명과 아이콘. 화면 라벨의 유일한 출처다. */
export const CATEGORIES = {
  korean_history: { label: '한국사 상식', icon: '🏯' },
  science: { label: '과학 상식', icon: '🔬' },
  world_geography: { label: '세계지리 상식', icon: '🌍' },
  arts_culture: { label: '예술·문화·시사', icon: '🎨' },
};

/** 출제·표시 순서. Object.keys 순서에 의존하지 않도록 명시한다. */
export const CATEGORY_ORDER = [
  'korean_history',
  'science',
  'world_geography',
  'arts_culture',
];

/**
 * 난이도 코드 → 쉬운 순서대로 나열.
 * 최하(bottom) → 하(low) → 중(mid) → 상(high) → 최상(top).
 * 이 배열의 인덱스 차이를 "난이도 거리"로 써서 인접 난이도를 보충하므로,
 * 순서를 바꾸면 storage.js의 보충 로직이 깨진다.
 */
export const DIFFICULTY_ORDER = ['bottom', 'low', 'mid', 'high', 'top'];

/** 분야별 400문항(각 100문항). 순서는 CATEGORY_ORDER를 따른다. */
export const QUESTION_BANK = [
  ...KOREAN_HISTORY,
  ...SCIENCE,
  ...WORLD_GEOGRAPHY,
  ...ARTS_CULTURE,
];
