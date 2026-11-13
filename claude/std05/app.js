// AI 공감 다이어리 — 클라이언트 (ES 모듈 하나, 빌드 없음)
//
// 계약: POST /api/analyze {"text"} → 200 {emotion, intensity, message, crisis}
//                                 실패 {"error": {"code", "message"}}
// 규칙: 사용자·서버가 만든 문자열은 모두 textContent로만 넣는다(innerHTML은 쓰지 않는다).

const MAX_LEN = 200;
const STORAGE_KEY = 'diary.v1.entries';
const CORRUPT_KEY = 'diary.v1.corrupt-backup';
const CLIENT_TIMEOUT_MS = 90_000;      // 서버의 TIMEOUT(504)보다 넉넉하게. 그래도 안 오면 직접 끊는다.
const MAX_ENTRIES = 1000;

// ── 감정 표 ──────────────────────────────────────────────
const EMOTIONS = {
  '기쁨':   { emoji: '😊', word: '기쁜 마음' },
  '설렘':   { emoji: '💗', word: '설레는 마음' },
  '평온':   { emoji: '🍃', word: '평온한 마음' },
  '슬픔':   { emoji: '💧', word: '슬픈 마음' },
  '불안':   { emoji: '🌀', word: '불안한 마음' },
  '분노':   { emoji: '🔥', word: '화가 난 마음' },
  '지침':   { emoji: '🌙', word: '지친 마음' },
  '외로움': { emoji: '🫂', word: '외로운 마음' },
};
const FALLBACK_EMOTION = { emoji: '📝', word: '마음' };
const INTENSITY_WORDS = ['', '살짝 느껴져요', '조금 느껴져요', '꽤 느껴져요', '많이 느껴져요', '아주 크게 느껴져요'];

function emotionInfo(name) {
  return Object.hasOwn(EMOTIONS, name) ? EMOTIONS[name] : FALLBACK_EMOTION;
}

// ── 오류 문구 ────────────────────────────────────────────
const ERROR_TEXT = {
  BAD_REQUEST: '보내 주신 글을 제대로 읽지 못했어요. 글을 조금 다듬어서 다시 보내 주세요.',
  TOO_LONG: `글이 조금 길어요. ${MAX_LEN}자 안으로 줄여서 다시 보내 주세요.`,
  NO_KEY: '지금은 AI와 이어질 준비가 되어 있지 않아요. 앱을 실행한 분께 API 키 설정을 확인해 달라고 부탁해 주세요.',
  RATE_LIMIT: '지금 이야기를 나누려는 분이 많아 잠시 쉬어 가야 해요. 1분쯤 뒤에 다시 보내 주세요.',
  TIMEOUT: '답장을 쓰는 데 너무 오래 걸려서 멈췄어요. 잠시 뒤에 다시 보내 주세요.',
  UPSTREAM: 'AI 쪽 사정으로 답장을 받지 못했어요. 잠시 뒤에 다시 보내 주세요.',
  BAD_OUTPUT: '답장이 제대로 만들어지지 않았어요. 한 번 더 보내 주시면 다시 써 볼게요.',
  NETWORK: '다이어리 서버와 연결되지 않았어요. 서버(./run.sh)가 켜져 있는지 확인한 뒤 다시 보내 주세요.',
  CLIENT_TIMEOUT: '너무 오래 기다려서 이번에는 그만 기다리기로 했어요. 잠시 뒤에 다시 보내 주세요.',
  UNKNOWN: '알 수 없는 문제가 생겨 답장을 받지 못했어요. 잠시 뒤에 다시 보내 주세요.',
};
// 코드가 없거나 모르는 코드일 때 HTTP 상태로 짐작한다.
function codeFromStatus(status) {
  if (status === 400 || status === 413 || status === 415) return 'BAD_REQUEST';
  if (status === 429) return 'RATE_LIMIT';
  if (status === 504) return 'TIMEOUT';
  if (status === 502 || status === 503) return 'UPSTREAM';
  return 'UNKNOWN';
}

const WAITING_PHRASES = [
  '적어 주신 하루를 천천히 읽고 있어요.',
  '그 순간의 마음을 가만히 떠올려 보고 있어요.',
  '어떤 말이 위로가 될지 고르고 있어요.',
  '따뜻한 답장을 한 글자씩 쓰고 있어요.',
  '조금만 더 기다려 주세요. 정성껏 쓰는 중이에요.',
];

// ── DOM ─────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const el = {
  form: $('entry-form'), input: $('entry-input'), submit: $('entry-submit'),
  count: $('entry-count'), hint: $('entry-hint'), today: $('today'),
  announcer: $('announcer'),
  waiting: $('waiting'), waitingPhrase: $('waiting-phrase'), waitingElapsed: $('waiting-elapsed'), cancel: $('cancel-button'),
  errorBox: $('error-box'), errorHeading: $('error-heading'), errorText: $('error-text'),
  result: $('result'), resultQuote: $('result-quote'), resultBadge: $('result-badge'),
  resultEmotion: $('result-emotion'), resultIntensity: $('result-intensity'),
  resultMessage: $('result-message'), resultCrisis: $('result-crisis'), resultSaved: $('result-saved'),
  historyList: $('history-list'), historyEmpty: $('history-empty'), historyCount: $('history-count'),
  storageNotice: $('storage-notice'),
  crisisTemplate: $('crisis-template'), bootNotice: $('boot-notice'),
};

// ── 상태 ────────────────────────────────────────────────
const state = {
  pending: null,        // { seq, controller, timers } — 기다리는 중일 때만
  seq: 0,               // 요청마다 1씩. 응답이 왔을 때 최신 요청인지 확인한다
  entries: [],          // 최신이 앞
  storageOk: true,
  confirmingId: null,   // 지우기 확인 중인 항목
};

// ── 저장소 (언제든 던질 수 있다: 차단·용량 초과·깨진 데이터) ─────────
const store = {
  get() {
    try { return window.localStorage.getItem(STORAGE_KEY); } catch { return undefined; }   // undefined = 쓸 수 없음
  },
  set(value) {
    try { window.localStorage.setItem(STORAGE_KEY, value); return { ok: true }; }
    catch (err) { return { ok: false, full: isQuotaError(err) }; }
  },
  backupCorrupt(raw) {
    try { window.localStorage.setItem(CORRUPT_KEY, raw); } catch { /* 백업 실패는 무시 */ }
  },
};

function isQuotaError(err) {
  return !!err && (err.name === 'QuotaExceededError' || err.name === 'NS_ERROR_DOM_QUOTA_REACHED' || err.code === 22 || err.code === 1014);
}

function isValidEntry(e) {
  return e && typeof e === 'object'
    && typeof e.id === 'string' && e.id
    && typeof e.at === 'string' && !Number.isNaN(Date.parse(e.at))
    && typeof e.text === 'string'
    && typeof e.emotion === 'string'
    && typeof e.message === 'string';
}

// 반환: { entries, problem } — problem은 사용자에게 보여줄 문장(없으면 null)
function loadEntries() {
  const raw = store.get();
  if (raw === undefined) {
    return { entries: [], available: false,
      problem: '이 브라우저에서는 일기를 저장할 수 없어요(저장소가 막혀 있어요). 답장은 그대로 받을 수 있지만, 창을 닫으면 사라져요.' };
  }
  if (raw === null) return { entries: [], available: true, problem: null };
  let parsed;
  try { parsed = JSON.parse(raw); } catch { parsed = undefined; }
  if (!Array.isArray(parsed)) {
    store.backupCorrupt(raw);
    return { entries: [], available: true, problem: '저장된 일기를 읽지 못했어요. 기록이 손상된 것 같아요. 새로 쓰는 일기부터 다시 모아 둘게요.' };
  }
  const good = parsed.filter(isValidEntry);
  good.sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  const dropped = parsed.length - good.length;
  return {
    entries: good, available: true,
    problem: dropped > 0 ? `읽을 수 없는 일기 ${dropped}개는 건너뛰었어요.` : null,
  };
}

function saveEntries(entries) {
  const res = store.set(JSON.stringify(entries));
  if (res.ok) return { ok: true, problem: null };
  return {
    ok: false,
    problem: res.full
      ? '저장 공간이 가득 차서 이 일기를 저장하지 못했어요. 지난 일기를 몇 개 지우면 다시 저장할 수 있어요.'
      : '이 브라우저에서 저장이 막혀 있어 이 일기를 저장하지 못했어요.',
  };
}

function newId() {
  try { if (crypto.randomUUID) return crypto.randomUUID(); } catch { /* 보안 맥락이 아니면 없을 수 있다 */ }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

// ── 날짜 표시 ────────────────────────────────────────────
const fmtDate = new Intl.DateTimeFormat('ko-KR', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'short' });
const fmtDateTime = new Intl.DateTimeFormat('ko-KR', { month: 'long', day: 'numeric', weekday: 'short', hour: 'numeric', minute: '2-digit' });
const fmtYearDateTime = new Intl.DateTimeFormat('ko-KR', { year: 'numeric', month: 'long', day: 'numeric', hour: 'numeric', minute: '2-digit' });
function formatAt(iso) {
  const d = new Date(iso);
  return (d.getFullYear() === new Date().getFullYear() ? fmtDateTime : fmtYearDateTime).format(d);
}

// ── 알림 ────────────────────────────────────────────────
let announceTimer = 0;
function announce(text) {
  // 같은 문장이 연달아 와도 다시 읽히게 비웠다가 넣는다
  el.announcer.textContent = '';
  clearTimeout(announceTimer);
  announceTimer = setTimeout(() => { el.announcer.textContent = text; }, 60);
}

// ── 입력 ────────────────────────────────────────────────
function updateCount() {
  const left = MAX_LEN - el.input.value.length;
  el.count.textContent = left > 0 ? `${left}자 더 쓸 수 있어요` : '200자를 다 채웠어요';
  el.count.classList.toggle('is-low', left <= 20);
}

function showHint(text) {
  el.hint.textContent = text;
  el.hint.hidden = !text;
  el.input.setAttribute('aria-invalid', text ? 'true' : 'false');
}

el.input.addEventListener('input', () => {
  updateCount();
  if (!el.hint.hidden && el.input.value.trim()) showHint('');
});

// 한글 조합 중에 누른 엔터는 글자를 확정하는 엔터이지 "보내기"가 아니다.
el.input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && (e.isComposing || e.keyCode === 229)) e.preventDefault();
});

el.form.addEventListener('submit', (e) => {
  e.preventDefault();
  if (state.pending) return;                 // 중복 제출 막기
  const text = el.input.value.trim();
  if (!text) {
    showHint('한 글자라도 적어 주시면 마음을 읽어 볼게요.');
    el.input.focus();
    return;
  }
  if (text.length > MAX_LEN) {
    showHint(`${MAX_LEN}자 안으로 줄여 주세요.`);
    return;
  }
  showHint('');
  analyze(text);
});

el.cancel.addEventListener('click', () => {
  if (!state.pending) return;
  finishPending();
  announce('기다리기를 멈췄어요. 다시 보내실 수 있어요.');
  el.input.focus();
});

// ── 요청 ────────────────────────────────────────────────
async function analyze(text) {
  const seq = ++state.seq;
  const controller = new AbortController();
  const startedAt = Date.now();
  state.pending = { seq, controller, timers: [] };
  renderWaiting(startedAt);

  const killer = setTimeout(() => controller.abort('client-timeout'), CLIENT_TIMEOUT_MS);
  let outcome;
  try {
    const res = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ text }),
      signal: controller.signal,
    });
    let body = null;
    try { body = await res.json(); } catch { body = null; }   // JSON이 아니어도 깨지지 않게
    outcome = interpret(res, body);
  } catch (err) {
    if (controller.signal.aborted) {
      outcome = controller.signal.reason === 'client-timeout' ? { error: 'CLIENT_TIMEOUT' } : { cancelled: true };
    } else {
      outcome = { error: 'NETWORK' };
    }
  } finally {
    clearTimeout(killer);
  }

  // 그사이 사용자가 그만 기다리거나 새로 보냈다면 이 응답은 버린다
  if (seq !== state.seq || outcome.cancelled) return;
  finishPending();

  if (outcome.error) renderError(outcome.error);
  else handleSuccess(text, outcome.data);
}

function interpret(res, body) {
  if (res.ok) {
    const data = normalizeResult(body);
    return data ? { data } : { error: 'BAD_OUTPUT' };
  }
  const code = body && body.error && typeof body.error.code === 'string' ? body.error.code : '';
  return { error: Object.hasOwn(ERROR_TEXT, code) ? code : codeFromStatus(res.status) };
}

function normalizeResult(body) {
  if (!body || typeof body !== 'object') return null;
  const message = typeof body.message === 'string' ? body.message.trim() : '';
  if (!message) return null;
  const emotion = typeof body.emotion === 'string' && body.emotion.trim() ? body.emotion.trim().slice(0, 20) : '마음';
  const n = Number(body.intensity);
  const intensity = Number.isInteger(n) && n >= 1 && n <= 5 ? n : null;
  return { emotion, intensity, message, crisis: body.crisis === true };
}

function finishPending() {
  const p = state.pending;
  if (!p) return;
  p.timers.forEach(clearInterval);
  p.controller.abort('cancelled');
  state.pending = null;
  // 취소한 요청의 응답이 뒤늦게 와도 버려지도록 seq를 올린다(정상 완료 때도 해가 없다)
  state.seq++;
  el.waiting.hidden = true;
  el.form.removeAttribute('aria-busy');
  el.submit.disabled = false;
  el.submit.textContent = '마음 전하기';
}

// ── 그리기 ──────────────────────────────────────────────
function renderWaiting(startedAt) {
  el.errorBox.hidden = true;
  el.result.hidden = true;
  el.submit.disabled = true;
  el.submit.textContent = '답장 기다리는 중…';
  el.form.setAttribute('aria-busy', 'true');
  el.waiting.hidden = false;

  let i = 0;
  el.waitingPhrase.textContent = WAITING_PHRASES[0];
  // 2026-11-13 실측(ernie, 23건): 중앙값 약 3초, 재시도·폴백까지 서버 예산 40초.
  const USUAL = '보통 몇 초면 와요. 길어도 40초 안쪽이에요.';
  el.waitingElapsed.textContent = USUAL;
  const phraseTimer = setInterval(() => {
    i = Math.min(i + 1, WAITING_PHRASES.length - 1);
    el.waitingPhrase.textContent = WAITING_PHRASES[i];
  }, 6000);
  const clockTimer = setInterval(() => {
    const s = Math.floor((Date.now() - startedAt) / 1000);
    el.waitingElapsed.textContent = s < 5 ? USUAL : `${s}초째 쓰고 있어요. 조금 오래 걸리네요. 길어도 40초 안에는 결과를 알려 드릴게요.`;
  }, 1000);
  state.pending.timers.push(phraseTimer, clockTimer);
  announce('일기를 보냈어요. 답장을 쓰는 동안 잠시 기다려 주세요.');
}

function renderError(code) {
  el.result.hidden = true;
  el.errorHeading.textContent = code === 'NETWORK' ? '서버와 이어지지 않았어요' : '답장을 받지 못했어요';
  el.errorText.textContent = `${ERROR_TEXT[code] || ERROR_TEXT.UNKNOWN} 쓰신 글은 입력칸에 그대로 남아 있어요.`;
  el.errorBox.hidden = false;
  announce(`${el.errorHeading.textContent}. ${el.errorText.textContent}`);
}

function fillIntensity(container, intensity) {
  container.replaceChildren();
  if (!intensity) return;
  const dots = document.createElement('span');
  dots.className = 'intensity__dots';
  dots.setAttribute('aria-hidden', 'true');
  for (let k = 1; k <= 5; k++) {
    const d = document.createElement('span');
    d.className = k <= intensity ? 'intensity__dot is-on' : 'intensity__dot';
    dots.append(d);
  }
  const words = document.createElement('span');
  words.textContent = `${INTENSITY_WORDS[intensity]} (5단계 중 ${intensity})`;
  container.append(dots, words);
}

function handleSuccess(text, data) {
  const info = emotionInfo(data.emotion);
  const entry = {
    id: newId(), at: new Date().toISOString(), text,
    emotion: data.emotion, intensity: data.intensity, message: data.message, crisis: data.crisis,
  };

  // 저장을 먼저 시도하고, 성공했을 때만 목록에 넣는다(실패했는데 저장된 것처럼 보이지 않게)
  let saved = { ok: false, problem: null };
  if (state.storageOk) {
    const next = [entry, ...state.entries].slice(0, MAX_ENTRIES);
    saved = saveEntries(next);
    if (saved.ok) {
      const removed = state.entries.length + 1 - next.length;
      state.entries = next;
      for (let k = 0; k < removed; k++) el.historyList.lastElementChild?.remove();
      el.historyList.prepend(buildEntry(entry));
      renderHistoryMeta();
    }
  } else {
    saved.problem = '저장소가 막혀 있어 이 일기는 저장되지 않았어요.';
  }

  // 결과 카드
  el.result.dataset.emotion = data.emotion;
  el.resultQuote.textContent = text;
  el.resultBadge.textContent = info.emoji;
  el.resultEmotion.textContent = data.emotion;
  fillIntensity(el.resultIntensity, data.intensity);
  el.resultMessage.textContent = data.message;
  el.resultCrisis.replaceChildren();
  if (data.crisis) el.resultCrisis.append(el.crisisTemplate.content.cloneNode(true));
  el.resultSaved.textContent = saved.ok ? '📔 지난 일기에 담아 두었어요.' : `⚠️ ${saved.problem}`;
  el.resultSaved.classList.toggle('is-problem', !saved.ok);
  el.errorBox.hidden = true;
  el.result.hidden = false;

  if (saved.ok) {
    el.input.value = '';
    updateCount();
  }

  const crisisLine = data.crisis ? ' 도움받을 수 있는 전화번호도 함께 적어 두었어요.' : '';
  const savedLine = saved.ok ? ' 지난 일기에 저장했어요.' : ` ${saved.problem}`;
  const strength = data.intensity ? `, ${INTENSITY_WORDS[data.intensity]}` : '';
  announce(`답장이 왔어요. 오늘의 감정은 ${data.emotion}${strength}. ${data.message}${crisisLine}${savedLine}`);
  el.result.scrollIntoView({ block: 'nearest', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
}

function prefersReducedMotion() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

// ── 지난 일기 ────────────────────────────────────────────
function buildEntry(entry) {
  const info = emotionInfo(entry.emotion);
  const li = document.createElement('li');
  li.className = 'entry';
  li.dataset.id = entry.id;
  li.dataset.emotion = entry.emotion;
  li.tabIndex = -1;

  const top = document.createElement('div');
  top.className = 'entry__top';
  const time = document.createElement('time');
  time.className = 'entry__time';
  time.dateTime = entry.at;
  time.textContent = formatAt(entry.at);
  const chip = document.createElement('span');
  chip.className = 'entry__chip';
  const emo = document.createElement('span');
  emo.setAttribute('aria-hidden', 'true');
  emo.textContent = info.emoji;
  const name = document.createElement('span');
  const n = Number(entry.intensity);
  name.textContent = Number.isInteger(n) && n >= 1 && n <= 5 ? `${entry.emotion} · ${n}/5` : entry.emotion;
  chip.append(emo, name);
  top.append(time, chip);

  const text = document.createElement('p');
  text.className = 'entry__text';
  text.textContent = entry.text;

  const details = document.createElement('details');
  details.className = 'entry__details';
  const summary = document.createElement('summary');
  summary.textContent = '답장 다시 읽기';
  const msg = document.createElement('p');
  msg.className = 'entry__message';
  msg.textContent = entry.message;
  details.append(summary, msg);
  if (entry.crisis === true) {
    const help = document.createElement('p');
    help.className = 'entry__help';
    help.append('도움받을 곳: 자살예방상담전화 ');
    help.append(telLink('109', 'tel:109'));
    help.append('(24시간), 정신건강위기상담 ');
    help.append(telLink('1577-0199', 'tel:1577-0199'));
    details.append(help);
  }

  const actions = document.createElement('div');
  actions.className = 'entry__actions';
  actions.append(deleteButton(entry));

  const bottom = document.createElement('div');
  bottom.className = 'entry__bottom';
  bottom.append(details, actions);
  li.append(top, text, bottom);
  return li;
}

function telLink(label, href) {
  const a = document.createElement('a');
  a.href = href;
  a.textContent = label;
  return a;
}

function deleteButton(entry) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'btn btn--ghost btn--small';
  b.dataset.action = 'ask-delete';
  b.textContent = '지우기';
  b.setAttribute('aria-label', `${formatAt(entry.at)} 일기 지우기`);
  return b;
}

function renderActions(li, confirming) {
  const actions = li.querySelector('.entry__actions');
  const entry = state.entries.find((e) => e.id === li.dataset.id);
  if (!actions || !entry) return;
  actions.classList.toggle('is-confirming', confirming);
  if (!confirming) {
    actions.replaceChildren(deleteButton(entry));
    return;
  }
  const q = document.createElement('p');
  q.className = 'entry__confirm-text';
  q.id = `confirm-${entry.id}`;
  q.textContent = '이 일기를 지울까요? 지우면 되돌릴 수 없어요.';
  const no = document.createElement('button');
  no.type = 'button';
  no.className = 'btn btn--ghost btn--small';
  no.dataset.action = 'cancel-delete';
  no.textContent = '그대로 둘게요';
  no.setAttribute('aria-describedby', q.id);
  const yes = document.createElement('button');
  yes.type = 'button';
  yes.className = 'btn btn--danger btn--small';
  yes.dataset.action = 'confirm-delete';
  yes.textContent = '지울게요';
  yes.setAttribute('aria-describedby', q.id);
  actions.replaceChildren(q, no, yes);
  no.focus();
}

function closeConfirm(focusBack) {
  if (!state.confirmingId) return;
  const li = findItem(state.confirmingId);
  state.confirmingId = null;
  if (li) {
    renderActions(li, false);
    if (focusBack) li.querySelector('[data-action="ask-delete"]')?.focus();
  }
}

function findItem(id) {
  for (const li of el.historyList.children) if (li.dataset.id === id) return li;
  return null;
}

function deleteEntry(li) {
  const id = li.dataset.id;
  const next = state.entries.filter((e) => e.id !== id);
  const res = saveEntries(next);
  if (!res.ok) {
    state.confirmingId = null;
    renderActions(li, false);
    showStorageNotice('저장소에 쓸 수 없어 일기를 지우지 못했어요. 잠시 뒤에 다시 해 주세요.');
    announce('일기를 지우지 못했어요.');
    return;
  }
  state.entries = next;
  state.confirmingId = null;
  const after = li.nextElementSibling || li.previousElementSibling;
  li.remove();
  renderHistoryMeta();
  announce('일기를 지웠어요.');
  const target = after?.querySelector('[data-action="ask-delete"]');
  if (target) target.focus();
  else el.input.focus();
}

el.historyList.addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-action]');
  if (!btn) return;
  const li = btn.closest('.entry');
  if (!li) return;
  const action = btn.dataset.action;
  if (action === 'ask-delete') {
    if (state.confirmingId && state.confirmingId !== li.dataset.id) closeConfirm(false);
    state.confirmingId = li.dataset.id;
    renderActions(li, true);
  } else if (action === 'cancel-delete') {
    closeConfirm(true);
  } else if (action === 'confirm-delete') {
    deleteEntry(li);
  }
});

el.historyList.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && state.confirmingId) {
    e.preventDefault();
    closeConfirm(true);
  }
});

function renderHistoryMeta() {
  const n = state.entries.length;
  el.historyEmpty.hidden = n > 0;
  el.historyCount.textContent = n > 0 ? `모두 ${n}편` : '';
}

function renderHistory() {
  const frag = document.createDocumentFragment();
  for (const entry of state.entries) frag.append(buildEntry(entry));
  el.historyList.replaceChildren(frag);
  state.confirmingId = null;
  renderHistoryMeta();
}

function showStorageNotice(text) {
  el.storageNotice.textContent = text || '';
  el.storageNotice.hidden = !text;
}

// 다른 탭에서 일기를 쓰거나 지웠을 때 목록을 맞춘다
window.addEventListener('storage', (e) => {
  if (e.key !== STORAGE_KEY && e.key !== null) return;
  const loaded = loadEntries();
  state.entries = loaded.entries;
  renderHistory();
});

// ── 시작 ────────────────────────────────────────────────
function init() {
  el.today.textContent = fmtDate.format(new Date());
  updateCount();

  const loaded = loadEntries();
  state.entries = loaded.entries;
  state.storageOk = loaded.available;
  showStorageNotice(loaded.problem);
  renderHistory();

  if (location.protocol === 'file:') {
    // 파일로 직접 열면 /api/analyze가 없다. 안내를 바로 보여 주고 보내기는 막아 둔다.
    el.bootNotice.classList.add('is-shown');
    el.submit.disabled = true;
    el.input.disabled = true;
    return;
  }
  document.documentElement.classList.add('app-ready');
  el.submit.disabled = false;
}

init();
