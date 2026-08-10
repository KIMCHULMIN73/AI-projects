// [모듈2] 화면(View) 계층 — 순수 렌더 함수만.
// 게임 규칙도, 이벤트 바인딩도 여기 없다. 데이터는 전부 인자로 받고,
// 보기 버튼에는 data-index만 부여한다(실제 클릭 바인딩은 모듈3의 main.js가 한다).

import { CATEGORIES, CATEGORY_ORDER } from '../data/questions.js';

/* ── 배경 ──────────────────────────────────────────────────────
   PRD §8은 assets/bg/*.jpg 가 미리 배치돼 있다고 전제하지만, 파일이 없을 수도 있다.
   그래서 항상 분야별 그라디언트를 먼저 깔고, 실제 이미지가 있으면 그때 덮어쓴다.
   → 에셋이 없어도 화면이 비지 않고, 나중에 파일만 넣으면 코드 수정 없이 이미지가 쓰인다. */
const BG_FALLBACK = {
  start:
    'radial-gradient(circle at 20% 20%, #ff8a5c 0%, transparent 45%),' +
    'radial-gradient(circle at 80% 30%, #ffd166 0%, transparent 40%),' +
    'linear-gradient(160deg, #6a3de8 0%, #3b1e7a 55%, #1b0f3d 100%)',
  korean_history:
    'radial-gradient(circle at 75% 20%, rgba(255, 214, 140, .45) 0%, transparent 45%),' +
    'linear-gradient(160deg, #7c2d12 0%, #9a3412 40%, #451a03 100%)',
  science:
    'radial-gradient(circle at 25% 25%, rgba(94, 234, 212, .35) 0%, transparent 45%),' +
    'radial-gradient(circle at 80% 70%, rgba(129, 140, 248, .4) 0%, transparent 45%),' +
    'linear-gradient(160deg, #0f172a 0%, #1e1b4b 55%, #020617 100%)',
  world_geography:
    'radial-gradient(circle at 30% 70%, rgba(45, 212, 191, .4) 0%, transparent 50%),' +
    'linear-gradient(160deg, #075985 0%, #0e7490 45%, #042f2e 100%)',
  arts_culture:
    'radial-gradient(circle at 70% 25%, rgba(244, 114, 182, .42) 0%, transparent 45%),' +
    'linear-gradient(160deg, #581c87 0%, #86198f 45%, #2e1065 100%)',
};

const BG_KEYS = Object.keys(BG_FALLBACK);

/** key → Promise<boolean> (해당 배경 이미지 파일이 실제로 있는지). 한 번만 조사한다. */
const bgProbeCache = new Map();
/** 비동기 이미지 로드가 늦게 끝나 "지난 배경"을 덮어쓰는 일을 막는 가드. */
let currentBgKey = null;

function probeBackgroundImage(key) {
  if (bgProbeCache.has(key)) return bgProbeCache.get(key);
  const probe = new Promise((resolve) => {
    const image = new Image();
    image.onload = () => resolve(true);
    image.onerror = () => resolve(false);
    image.src = `assets/bg/${key}.jpg`;
  });
  bgProbeCache.set(key, probe);
  return probe;
}

/* ── DOM 참조 ─────────────────────────────────────────────── */
const $ = (selector) => document.querySelector(selector);

const screens = {
  start: $('#screen-start'),
  play: $('#screen-play'),
  result: $('#screen-result'),
  ranking: $('#screen-ranking'),
};

const el = {
  bgLayer: $('#bg-layer'),
  categoryCards: $('#categoryCards'),
  playerName: $('#playerName'),

  hudName: $('#hudName'),
  hudProgress: $('#hudProgress'),
  hudScore: $('#hudScore'),
  hudProgressBar: $('#hudProgressBar'),
  progressTrack: $('.progress-track'),
  topbarScore: $('.topbar-score'),

  questionCategory: $('#questionCategory'),
  questionPoints: $('#questionPoints'),
  questionText: $('#questionText'),
  choices: $('#choices'),

  feedbackOverlay: $('#feedbackOverlay'),
  feedbackPopup: $('#feedbackPopup'),
  feedbackMark: $('#feedbackMark'),
  feedbackTitle: $('#feedbackTitle'),
  feedbackDelta: $('#feedbackDelta'),
  feedbackAnswer: $('#feedbackAnswer'),
  feedbackExplanation: $('#feedbackExplanation'),

  resultPlayer: $('#resultPlayer'),
  resultScore: $('#resultScore'),
  resultCorrect: $('#resultCorrect'),
  resultAccuracy: $('#resultAccuracy'),
  categoryScores: $('#categoryScores'),

  rankingBody: $('#rankingBody'),
};

const MEDALS = ['🥇', '🥈', '🥉'];

/** 하나만 보이게 하고 나머지는 hidden. */
function showScreen(name) {
  Object.entries(screens).forEach(([key, node]) => {
    node.hidden = key !== name;
  });
  window.scrollTo(0, 0);
}

function toPercent(ratio) {
  return `${Math.round((Number(ratio) || 0) * 100)}%`;
}

export const Views = {
  /** 시작 화면. 분야 카드는 CATEGORIES를 근거로 그린다(라벨 하드코딩 금지). */
  showStart() {
    el.categoryCards.replaceChildren(
      ...CATEGORY_ORDER.map((category) => {
        const { label, icon } = CATEGORIES[category];
        const item = document.createElement('li');
        item.className = 'category-card';
        item.dataset.category = category;

        const iconNode = document.createElement('span');
        iconNode.className = 'cc-icon';
        iconNode.setAttribute('aria-hidden', 'true');
        iconNode.textContent = icon;

        const labelNode = document.createElement('span');
        labelNode.className = 'cc-label';
        labelNode.textContent = label;

        item.append(iconNode, labelNode);
        return item;
      })
    );

    this.setBackground('start');
    showScreen('start');
  },

  /**
   * 배경을 해당 분야(또는 'start')로 전환한다.
   * 그라디언트를 즉시 깔고, assets/bg/<key>.jpg 가 존재하면 비동기로 덮어쓴다.
   */
  setBackground(category) {
    const key = BG_KEYS.includes(category) ? category : 'start';
    if (currentBgKey === key) return;
    currentBgKey = key;

    el.bgLayer.style.backgroundImage = BG_FALLBACK[key];

    probeBackgroundImage(key).then((exists) => {
      if (!exists || currentBgKey !== key) return;
      el.bgLayer.style.backgroundImage = `url("assets/bg/${key}.jpg")`;
    });
  },

  /** 배경 이미지를 미리 조사·캐시해 전환 시 깜빡임을 줄인다 (PRD F-12). */
  preloadBackgrounds() {
    BG_KEYS.forEach(probeBackgroundImage);
  },

  /**
   * 문제와 보기를 그리고 상단 바를 갱신한다.
   * @param {object} q 문제 객체
   * @param {{name:string, index:number, total:number, score:number}} state index는 0-based
   */
  showQuestion(q, state) {
    const { name, index, total, score, points } = state;
    const current = index + 1;

    el.hudName.textContent = name;
    el.hudProgress.textContent = `${current} / ${total}`;
    el.hudScore.textContent = String(score);

    const ratio = total > 0 ? current / total : 0;
    el.hudProgressBar.style.width = `${ratio * 100}%`;
    el.progressTrack.setAttribute('aria-valuemax', String(total));
    el.progressTrack.setAttribute('aria-valuenow', String(current));

    const meta = CATEGORIES[q.category] || { label: '', icon: '' };
    el.questionCategory.querySelector('.qc-icon').textContent = meta.icon;
    el.questionCategory.querySelector('.qc-label').textContent = meta.label;

    // 배점은 모듈3이 계산해 넘겨 준 값을 그대로 그린다(점수 규칙은 뷰의 소관이 아니다).
    // 값에 따라 등급 클래스를 달아 색만 달리한다.
    const value = Number.isFinite(points) ? points : 0;
    el.questionPoints.querySelector('.qp-value').textContent = String(value);
    el.questionPoints.dataset.tier = q.difficulty || '';
    el.questionPoints.setAttribute('aria-label', `배점 ${value}점`);
    el.questionPoints.hidden = value <= 0;

    el.questionText.textContent = q.question;

    el.choices.replaceChildren(
      ...q.choices.map((choice, choiceIndex) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'choice';
        button.dataset.index = String(choiceIndex); // 바인딩은 main.js가 한다

        const num = document.createElement('span');
        num.className = 'choice-num';
        num.textContent = String(choiceIndex + 1);

        const text = document.createElement('span');
        text.className = 'choice-text';
        text.textContent = choice;

        button.append(num, text);
        return button;
      })
    );

    el.feedbackOverlay.hidden = true;
    showScreen('play');
  },

  /** 점수 숫자를 한 번 튀어오르게 한다(값 변경은 다음 showQuestion에서 반영). */
  bumpScore() {
    el.topbarScore.classList.remove('is-bumped');
    void el.topbarScore.offsetWidth; // 리플로우로 애니메이션 재시작
    el.topbarScore.classList.add('is-bumped');
  },

  /**
   * 정답/오답 팝업 + 해설. 효과음은 호출하지 않는다(모듈3 담당).
   * @param {boolean} isCorrect
   * @param {string} explanation
   * @param {{delta?:number, selectedIndex?:number, answerIndex?:number}} [options]
   */
  showFeedback(isCorrect, explanation, options = {}) {
    const { delta, selectedIndex, answerIndex } = options;

    // 보기 버튼에 채점 결과를 칠하고 더 못 누르게 잠근다.
    Array.from(el.choices.children).forEach((button) => {
      const index = Number(button.dataset.index);
      button.disabled = true;
      if (index === answerIndex) {
        button.classList.add('is-correct');
      } else if (index === selectedIndex) {
        button.classList.add('is-wrong');
      } else {
        button.classList.add('is-dimmed');
      }
    });

    el.feedbackPopup.classList.toggle('is-correct', isCorrect);
    el.feedbackPopup.classList.toggle('is-wrong', !isCorrect);
    el.feedbackMark.textContent = isCorrect ? '○' : '✕';
    el.feedbackTitle.textContent = isCorrect ? '정답!' : '오답!';

    if (typeof delta === 'number') {
      el.feedbackDelta.textContent = `${delta > 0 ? '+' : ''}${delta}점`;
      el.feedbackDelta.hidden = false;
    } else {
      el.feedbackDelta.hidden = true;
    }

    // 틀렸을 때만 정답이 무엇이었는지 알려 준다.
    const answerText =
      !isCorrect && typeof answerIndex === 'number'
        ? el.choices.children[answerIndex]?.querySelector('.choice-text')?.textContent
        : null;
    if (answerText) {
      el.feedbackAnswer.textContent = `정답: ${answerText}`;
      el.feedbackAnswer.hidden = false;
    } else {
      el.feedbackAnswer.hidden = true;
    }

    el.feedbackExplanation.textContent = explanation || '';
    el.feedbackOverlay.hidden = false;
  },

  /** 피드백 팝업만 닫는다(다음 문제로 넘어가기 직전). */
  hideFeedback() {
    el.feedbackOverlay.hidden = true;
  },

  /**
   * 완료 화면.
   * @param {{name?:string, score:number, correctCount:number, total:number,
   *          accuracy:number, byCategory:Object<string, number>}} summary
   */
  showResult(summary) {
    const { name, score, correctCount, total, accuracy, byCategory = {} } = summary;

    el.resultPlayer.textContent = name ? `${name} 도전자의 성적표` : '';
    el.resultScore.textContent = String(score);
    el.resultCorrect.textContent = `${correctCount} / ${total}`;
    el.resultAccuracy.textContent = toPercent(accuracy);

    el.categoryScores.replaceChildren(
      ...CATEGORY_ORDER.map((category) => {
        const { label, icon } = CATEGORIES[category];
        const ratio = Number(byCategory[category]) || 0;

        const row = document.createElement('li');
        row.className = 'cs-row';

        const iconNode = document.createElement('span');
        iconNode.className = 'cs-icon';
        iconNode.setAttribute('aria-hidden', 'true');
        iconNode.textContent = icon;

        const labelNode = document.createElement('span');
        labelNode.className = 'cs-label';
        labelNode.textContent = label;

        const track = document.createElement('span');
        track.className = 'cs-track';
        const fill = document.createElement('span');
        fill.className = 'cs-fill';
        fill.style.width = `${ratio * 100}%`;
        track.append(fill);

        const value = document.createElement('span');
        value.className = 'cs-value';
        value.textContent = toPercent(ratio);

        row.append(iconNode, labelNode, track, value);
        return row;
      })
    );

    this.setBackground('start');
    showScreen('result');
  },

  /**
   * 랭킹 화면.
   * @param {Array<{name:string, score:number}>} list 이미 정렬된 목록
   * @param {{highlightIndex?:number}} [options] 방금 저장된 내 기록 강조
   */
  showRanking(list, options = {}) {
    const { highlightIndex } = options;

    if (!Array.isArray(list) || list.length === 0) {
      const row = document.createElement('tr');
      row.className = 'empty-row';
      const cell = document.createElement('td');
      cell.colSpan = 3;
      cell.textContent = '아직 기록이 없습니다. 첫 번째 도전자가 되어 보세요!';
      row.append(cell);
      el.rankingBody.replaceChildren(row);
    } else {
      el.rankingBody.replaceChildren(
        ...list.map((record, index) => {
          const row = document.createElement('tr');
          if (index < 3) row.classList.add(`rank-${index + 1}`);
          if (index === highlightIndex) row.classList.add('is-me');

          const rank = document.createElement('td');
          rank.className = 'rank-cell';
          rank.textContent = index < 3 ? MEDALS[index] : String(index + 1);

          const name = document.createElement('td');
          name.textContent = record.name;

          const score = document.createElement('td');
          score.className = 'score-cell';
          score.textContent = String(record.score);

          row.append(rank, name, score);
          return row;
        })
      );
    }

    this.setBackground('start');
    showScreen('ranking');
  },

  /** 시작 화면 입력창을 비우고 포커스를 준다. */
  focusNameInput() {
    el.playerName.focus();
  },
};
