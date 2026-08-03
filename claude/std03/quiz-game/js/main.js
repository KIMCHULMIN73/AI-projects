// [모듈3] 부팅 & 이벤트 연결.
// 뷰는 그리기만 하고 로직은 game.js에 있으므로, 둘을 잇는 배선은 전부 여기 모아 둔다.

import { Views } from './views.js';
import { Game } from './game.js';
import { Audio } from './audio.js';

const $ = (selector) => document.querySelector(selector);

function bind() {
  const startForm = $('#startForm');
  const playerName = $('#playerName');
  const startBtn = $('#startBtn');
  const startHint = $('#startHint');
  const choices = $('#choices');
  const feedbackOverlay = $('#feedbackOverlay');

  /* ── 시작 화면 ─────────────────────────────────────────── */
  const syncStartButton = () => {
    const hasName = playerName.value.trim().length > 0;
    startBtn.disabled = !hasName;
    startHint.classList.remove('is-warning');
    startHint.textContent = hasName
      ? '준비되면 게임 시작을 누르세요.'
      : '이름을 입력하면 시작할 수 있어요.';
  };

  playerName.addEventListener('input', syncStartButton);

  startForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const name = playerName.value.trim();
    if (!name) {
      startHint.textContent = '도전자 이름을 먼저 입력해 주세요.';
      startHint.classList.add('is-warning');
      playerName.focus();
      return;
    }
    Game.start(name);
  });

  /* ── 게임 화면: 보기 버튼 (이벤트 위임) ────────────────── */
  choices.addEventListener('click', (event) => {
    const button = event.target.closest('.choice');
    if (!button || button.disabled) return;
    Game.answer(button.dataset.index);
  });

  /* ── 피드백 팝업 ───────────────────────────────────────── */
  $('#nextBtn').addEventListener('click', () => Game.next());

  /* ── 완료 / 랭킹 화면 ──────────────────────────────────── */
  $('#retryBtn').addEventListener('click', () => Game.retry());
  $('#finishBtn').addEventListener('click', () => Game.finish());
  $('#restartBtn').addEventListener('click', () => {
    Game.restart();
    playerName.value = '';
    syncStartButton();
  });

  /* ── 키보드 단축키 ─────────────────────────────────────── */
  document.addEventListener('keydown', (event) => {
    // 이름 입력 중에는 단축키가 글자를 가로채지 않게 한다.
    if (event.target instanceof HTMLInputElement) return;

    if (event.key === 'm' || event.key === 'M') {
      showToast(Audio.setMuted(!Audio.isMuted()) ? '🔇 음소거' : '🔊 소리 켬');
      return;
    }

    // 팝업이 떠 있으면 Enter/Space는 "다음 문제"
    if (!feedbackOverlay.hidden) {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        Game.next();
      }
      return;
    }

    // 게임 중에는 1~4로 보기 선택
    if (['1', '2', '3', '4'].includes(event.key)) {
      const button = choices.children[Number(event.key) - 1];
      if (button && !button.disabled) Game.answer(button.dataset.index);
    }
  });

  syncStartButton();
}

/** 음소거 토글처럼 잠깐 알려 주면 되는 안내. CSS 파일을 건드리지 않도록 인라인 스타일만 쓴다. */
let toastTimer = null;
function showToast(message) {
  let toast = $('#toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'toast';
    toast.style.cssText = [
      'position:fixed', 'z-index:999', 'left:50%', 'top:24px', 'transform:translateX(-50%)',
      'background:rgba(16,18,31,.92)', 'color:#fff', 'font-weight:700', 'font-size:.9rem',
      'padding:10px 18px', 'border-radius:999px', 'pointer-events:none',
      'transition:opacity .2s ease',
    ].join(';');
    document.body.append(toast);
  }
  toast.textContent = message;
  toast.style.opacity = '1';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.style.opacity = '0';
  }, 1200);
}

function boot() {
  bind();
  Views.preloadBackgrounds(); // 분야 전환 시 깜빡임 방지 (PRD F-12)
  Views.showStart();

  // 콘솔에서 상태를 들여다볼 수 있게 열어 둔다(개발 편의).
  window.Game = Game;
}

// type="module" 스크립트는 defer라 보통 이 시점에 DOM이 준비돼 있지만, 방어적으로 확인한다.
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
