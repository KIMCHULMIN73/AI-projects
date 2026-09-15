// 3단계: 프로필과 레시피 보관함. (→ PRD_step3.md)
//
// 모델도 프록시 API도 부르지 않는다. 브라우저 안에서 끝난다.
// 1·2단계(app.js)와는 'fridge:recipes' 이벤트 하나로만 이어진다 — 이 파일과
// index.html의 3단계 조각을 지워도 1·2단계는 그대로 돈다.

import { KEYS, readJSON, writeJSON, removeKey } from './storage.js';

const EMOJIS = ['🐻', '🐰', '🦊', '🐼', '🐯', '🐸', '🐧', '🐨', '🐹', '🦁'];
const NAME_MAX = 20;
const MSG_QUOTA = '저장 공간이 찼습니다. 오래된 레시피를 지워 주세요.';
const MSG_BLOCKED = '이 브라우저가 저장을 막고 있습니다. 프로필과 보관함은 새로고침하면 사라집니다.';

const $ = (id) => document.getElementById(id);

let profiles = [];       // [{ id, name, emoji, createdAt }]
let activeId = null;
let saved = [];          // 지금 프로필의 [{ savedAt, recipe }] — 새로 저장한 것이 앞
let shown = new Map();   // 2단계 화면에 떠 있는 레시피 id → Recipe

// ── 읽기 · 정리 ─────────────────────────────────────────────────────────

function sanitizeProfiles(raw) {
  if (!Array.isArray(raw)) return [];
  const seen = new Set();
  const out = [];
  for (const p of raw) {
    if (!p || typeof p.id !== 'string' || !/^p-\d+$/.test(p.id) || seen.has(p.id)) continue;
    seen.add(p.id);
    out.push({
      id: p.id,
      name: cleanName(p.name) || '이름 없음',
      emoji: typeof p.emoji === 'string' && p.emoji ? p.emoji : EMOJIS[0],
      createdAt: typeof p.createdAt === 'string' ? p.createdAt : null,
    });
  }
  return out;
}

function sanitizeSaved(raw) {
  if (!Array.isArray(raw)) return [];
  const seen = new Set();
  const out = [];
  for (const s of raw) {
    const r = s?.recipe;
    if (!r || typeof r !== 'object' || typeof r.id !== 'string' || typeof r.title !== 'string' || seen.has(r.id)) continue;
    seen.add(r.id);
    out.push({ savedAt: typeof s.savedAt === 'string' ? s.savedAt : null, recipe: r });
  }
  return out;
}

const readSaved = (profileId) => sanitizeSaved(readJSON(KEYS.saved(profileId), []));
const cleanName = (v) => String(v ?? '').normalize('NFC').replace(/\s+/g, ' ').trim().slice(0, NAME_MAX);
const active = () => profiles.find((p) => p.id === activeId);

// ── 쓰기 — 3단계는 실패를 반드시 알린다 (→ UNIT_storage, PRD_step3 "저장이 실패할 때") ──

/** 'ok' | 'quota' | 'blocked' */
function persist(key, value) {
  const r = writeJSON(key, value);
  if (r.ok) {
    notice(null);
    return 'ok';
  }
  if (r.quota) {
    notice(MSG_QUOTA);
    openLibrary();          // 자동으로 오래된 것을 지우지 않는다. 사용자가 고르게 보낸다.
    return 'quota';
  }
  notice(MSG_BLOCKED);
  return 'blocked';
}

function notice(message) {
  const box = $('store-error');
  box.textContent = message || '';
  box.hidden = !message;
}

function nowIso() {
  const d = new Date();
  const pad = (n) => String(Math.floor(Math.abs(n))).padStart(2, '0');
  const off = -d.getTimezoneOffset();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
    + `${off >= 0 ? '+' : '-'}${pad(off / 60)}:${pad(off % 60)}`;
}

// ── 프로필 ──────────────────────────────────────────────────────────────

function newProfile(name) {
  const max = profiles.reduce((m, p) => Math.max(m, Number(p.id.slice(2)) || 0), 0);
  const used = new Set(profiles.map((p) => p.emoji));
  const emoji = EMOJIS.find((e) => !used.has(e)) ?? EMOJIS[profiles.length % EMOJIS.length];
  return { id: `p-${max + 1}`, name, emoji, createdAt: nowIso() };
}

/** 프로필 목록을 바꾼다. 용량 초과면 반영하지 않고, 저장 차단이면 메모리에서만 반영한다. */
function setProfiles(next) {
  if (persist(KEYS.profiles, next) === 'quota') return false;
  profiles = next;
  return true;
}

function setActive(id) {
  activeId = id;
  persist(KEYS.activeProfile, id);
  saved = readSaved(id);
}

function createProfile(name) {
  const p = newProfile(name);
  if (!setProfiles([...profiles, p])) return;
  setActive(p.id);
  renderAll();
}

function renameProfile(name) {
  if (setProfiles(profiles.map((p) => (p.id === activeId ? { ...p, name } : p)))) renderAll();
}

// 전환은 페이지를 새로 그리지 않는다. 재료 목록(app.js)은 건드리지 않고 보관함만 바꾼다.
function switchProfile(id) {
  if (id === activeId || !profiles.some((p) => p.id === id)) return;
  setActive(id);
  renderAll();
}

function deleteProfile() {
  if (profiles.length <= 1) return;                  // 마지막 하나는 지우지 않는다
  const p = active();
  const count = readSaved(p.id).length;
  if (!window.confirm(`${p.emoji} ${p.name} 프로필을 지웁니다.\n저장한 레시피 ${count}개도 함께 사라집니다. 계속할까요?`)) return;
  const next = profiles.filter((x) => x.id !== p.id);
  if (!setProfiles(next)) return;
  removeKey(KEYS.saved(p.id));                       // 남기면 localStorage가 계속 찬다
  setActive(next[0].id);                             // 지금 프로필을 지웠으니 남은 것 중 첫 번째로
  renderAll();
}

// ── 보관함 ──────────────────────────────────────────────────────────────

const isSaved = (id) => saved.some((s) => s.recipe.id === id);

function saveRecipe(id) {
  const recipe = shown.get(id);
  if (!recipe || isSaved(id)) return;                // 같은 id를 두 번 담지 않는다
  const next = [{ savedAt: nowIso(), recipe }, ...saved];
  if (persist(KEYS.saved(activeId), next) !== 'ok') return;   // 저장되지 않았으면 "저장됨"으로 보이지 않게
  saved = next;
  renderAll();
}

function unsaveRecipe(id) {
  const next = saved.filter((s) => s.recipe.id !== id);
  if (persist(KEYS.saved(activeId), next) !== 'ok') return;
  saved = next;
  renderAll();
}

function openLibrary() {
  const section = $('library');
  section.scrollIntoView({ block: 'start' });
  section.focus({ preventScroll: true });
}

// ── 그리기 ──────────────────────────────────────────────────────────────

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;   // 저장된 값도 모델 출력이다 — textContent로만
  return node;
}

function formatDate(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString('ko-KR', { dateStyle: 'short', timeStyle: 'short' });
}

function renderAll() {
  renderProfileBar();
  renderMenu();
  renderLibrary();
  decorateCards();
}

function renderProfileBar() {
  const p = active();
  $('profile-current').textContent = p ? `${p.emoji} ${p.name}` : '프로필';
  $('library-count').textContent = String(saved.length);
}

function renderMenu() {
  const ul = $('profile-list');
  ul.replaceChildren();
  for (const p of profiles) {
    const li = el('li');
    const btn = el('button', 'profile-item', `${p.emoji} ${p.name}`);
    btn.type = 'button';
    btn.setAttribute('aria-pressed', String(p.id === activeId));
    btn.append(el('span', 'profile-count', ` 레시피 ${p.id === activeId ? saved.length : readSaved(p.id).length}개`));
    btn.addEventListener('click', () => switchProfile(p.id));
    li.append(btn);
    ul.append(li);
  }
  const input = $('rename-input');
  if (document.activeElement !== input) input.value = active()?.name ?? '';
  const only = profiles.length <= 1;
  $('profile-delete').disabled = only;
  $('profile-delete-note').textContent = only ? '마지막 프로필은 지울 수 없습니다.' : '';
}

function chipRow(label, items, kind) {
  const wrap = el('div', `chips ${kind}`);
  const ul = el('ul');
  if (items.length) items.forEach((name) => ul.append(el('li', 'chip', name)));
  else ul.append(el('li', 'chip none', '없음'));
  wrap.append(el('span', 'chips-label', label), ul);
  return wrap;
}

const strList = (v) => (Array.isArray(v) ? v.filter((x) => typeof x === 'string') : []);

function recipeBody(r) {
  const body = el('div', 'saved-body');
  const meta = [
    Number.isFinite(r.time_minutes) ? `${r.time_minutes}분` : null,
    typeof r.difficulty === 'string' ? r.difficulty : null,
    Number.isFinite(r.servings) ? `${r.servings}인분` : null,
  ].filter(Boolean).join(' · ');
  if (meta) body.append(el('p', 'recipe-meta', meta));
  if (typeof r.summary === 'string' && r.summary) body.append(el('p', 'recipe-summary', r.summary));
  body.append(chipRow('가진 재료', strList(r.uses), 'have'), chipRow('부족한 재료', strList(r.missing), 'missing'));
  const ol = el('ol', 'recipe-steps');
  strList(r.steps).forEach((step) => ol.append(el('li', null, step)));
  body.append(ol);
  if (typeof r.tips === 'string' && r.tips) body.append(el('p', 'recipe-tips', `팁: ${r.tips}`));
  return body;
}

function renderLibrary() {
  const p = active();
  $('library-owner').textContent = p ? `${p.emoji} ${p.name} · ${saved.length}개` : '';
  $('library-empty').hidden = saved.length > 0;

  const ul = $('library-list');
  const open = new Set([...ul.querySelectorAll('details[open]')].map((d) => d.dataset.id));
  ul.replaceChildren();
  for (const { savedAt, recipe } of saved) {
    const li = el('li', 'saved-item');
    const details = el('details', 'saved');
    details.dataset.id = recipe.id;
    details.open = open.has(recipe.id);
    const summary = el('summary');
    summary.append(el('span', 'saved-title', recipe.title));
    const when = formatDate(savedAt);
    const bits = [when && `${when} 저장`, Number.isFinite(recipe.time_minutes) ? `${recipe.time_minutes}분` : null].filter(Boolean).join(' · ');
    if (bits) summary.append(el('span', 'saved-meta', bits));
    details.append(summary, recipeBody(recipe));

    const del = el('button', 'saved-del', '삭제');
    del.type = 'button';
    del.setAttribute('aria-label', `${recipe.title} 보관함에서 삭제`);
    del.addEventListener('click', () => unsaveRecipe(recipe.id));
    li.append(details, del);
    ul.append(li);
  }
}

/** 2단계 레시피 카드에 저장 버튼을 붙이고, 지금 프로필 기준으로 "저장됨" 여부를 칠한다. */
function decorateCards() {
  for (const card of document.querySelectorAll('#recipes .recipe[data-id]')) {
    const id = card.dataset.id;
    const recipe = shown.get(id);
    if (!recipe) continue;
    let btn = card.querySelector('.save-btn');
    if (!btn) {
      btn = el('button', 'save-btn');
      btn.type = 'button';
      btn.addEventListener('click', () => saveRecipe(id));
      (card.querySelector('.recipe-head') ?? card).append(btn);
    }
    const done = isSaved(id);
    btn.textContent = done ? '저장됨' : '저장';
    btn.disabled = done;
    btn.title = done ? `${active()?.name ?? ''} 보관함에 있습니다` : `${active()?.name ?? ''} 보관함에 저장`;
  }
}

// ── 시작 ────────────────────────────────────────────────────────────────

function wire() {
  const toggle = $('profile-toggle');
  const menu = $('profile-menu');
  const setOpen = (open) => {
    menu.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
  };
  toggle.addEventListener('click', () => setOpen(menu.hidden));
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !menu.hidden) {
      setOpen(false);
      toggle.focus();
    }
  });

  $('new-profile-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const name = cleanName(e.currentTarget.elements.name.value);
    if (!name) return;
    createProfile(name);
    e.currentTarget.reset();
  });
  $('rename-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const name = cleanName(e.currentTarget.elements.name.value);
    if (!name) return;
    renameProfile(name);
    $('rename-input').blur();
    renderMenu();
  });
  $('profile-delete').addEventListener('click', deleteProfile);
  $('library-link').addEventListener('click', openLibrary);

  // 2단계가 카드를 그렸다 — 저장 버튼을 붙인다.
  document.addEventListener('fridge:recipes', (e) => {
    shown = new Map((e.detail?.recipes ?? []).map((r) => [String(r.id), r]));
    decorateCards();
  });
}

function init() {
  profiles = sanitizeProfiles(readJSON(KEYS.profiles, []));
  if (!profiles.length) {
    // 첫 실행(또는 깨진 값에서 복구). 강제로 만들게 하지 않고 "기본"을 바로 쓰게 한다.
    profiles = [newProfile('기본')];
    persist(KEYS.profiles, profiles);
  }
  const stored = readJSON(KEYS.activeProfile, null);
  const id = profiles.some((p) => p.id === stored) ? stored : profiles[0].id;
  if (id === stored) {
    activeId = id;
    saved = readSaved(id);
  } else {
    setActive(id);
  }
  renderAll();
}

wire();
init();
