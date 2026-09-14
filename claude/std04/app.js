// 1단계 화면: 사진 → (프록시) → 식재료 목록 편집. (→ PRD_step1.md)
// 2단계 화면: 식재료 목록 → (프록시) → 레시피 카드. (→ PRD_step2.md)
//
// 이 파일은 키도 모델 이름도 모른다. /api/* 만 부른다.

import { KEYS, readJSON, writeJSON } from './storage.js';

// 프롬프트에 박는 목록이자 화면 그룹의 순서. server.py의 CATEGORIES와 같아야 한다.
// 늘리려면 프롬프트·값 검증(server.py)·화면(여기) 세 곳을 함께 고친다.
const CATEGORIES = ['채소', '과일', '육류', '해산물', '유제품', '달걀', '곡물면', '양념소스', '가공식품', '음료', '기타'];
const CONFIDENCE = ['high', 'medium', 'low'];

// 이미지 축소 규칙 (→ UNIT_image_input.md). 768px: 실측 22초. 1024급은 33초였다.
const MAX_EDGE = 768;
const JPEG_QUALITY = 0.85;
const MAX_DATA_URL = 4 * 1024 * 1024;

// 긴 대기 규칙 (→ UNIT_ui_state.md 단계별 표). 프록시가 총 예산으로 TIMEOUT을 돌려준다.
// 브라우저는 그걸 기다리고, 프록시가 아예 멈춘 경우에만 giveUp 뒤에 스스로 끊는다.
const WAIT = {
  vision: { expect: 20, slowAfter: 30, giveUp: 75 },
  recipe: { expect: 10, slowAfter: 20, giveUp: 40 },
};
const RECIPE_COUNT = 3;

// 프록시가 문구를 만들 수 없는 경우(프록시에 닿지 못함)만 화면이 직접 말한다.
const MSG_PROXY_DOWN = '프록시(server.py)에 연결할 수 없습니다. 서버가 켜져 있는지 확인하세요.';
const MSG_TIMEOUT = '시간이 너무 오래 걸립니다.';
const MSG_IDLE = '냉장고 사진을 올려 주세요. 분석은 보통 20초쯤 걸립니다.';
const MSG_SLOW = '평소보다 오래 걸리고 있습니다. 조금만 더 기다려 주세요.';

const $ = (id) => document.getElementById(id);

let list = loadList();   // IngredientList — 2단계가 받는 계약
let photo = null;        // { dataUrl, width, height, origWidth, origHeight } — 메모리에만 둔다
let recipes = [];        // 마지막 추천 결과(Recipe[]) — 메모리에만. 저장은 3단계 일이다.
let usedNames = [];      // 그 추천에 보낸 재료 이름

// 화면의 비동기 작업 두 개. 각자 상태 표시 요소와 진행 중인 작업을 가진다.
const panels = {
  vision: { status: 'status', elapsed: 'elapsed', error: 'error', errorMsg: 'error-msg', job: null },
  recipe: { status: 'recipe-status', elapsed: 'recipe-elapsed', error: 'recipe-error', errorMsg: 'recipe-error-msg', job: null },
};

// ── IngredientList ──────────────────────────────────────────────────────

function emptyList() {
  return { items: [], unsure: [], analyzed_at: null };
}

/** 저장소나 프록시에서 온 값을 계약 모양으로 맞춘다. 모양이 틀리면 빈 목록. */
function sanitizeList(raw) {
  if (!raw || typeof raw !== 'object' || !Array.isArray(raw.items)) return emptyList();
  const items = raw.items
    .filter((it) => it && typeof it === 'object')
    .map((it) => ({
      name: String(it.name ?? '').slice(0, 40),
      category: CATEGORIES.includes(it.category) ? it.category : '기타',
      quantity: String(it.quantity ?? '').slice(0, 40) || '미상',
      confidence: CONFIDENCE.includes(it.confidence) ? it.confidence : 'low',
      needs_review: Boolean(it.needs_review),
      source: it.source === 'user' ? 'user' : 'vision',
    }));
  const unsure = Array.isArray(raw.unsure)
    ? raw.unsure.filter((u) => typeof u === 'string' && u.trim())
    : [];
  const analyzed_at = typeof raw.analyzed_at === 'string' ? raw.analyzed_at : null;
  return { items, unsure, analyzed_at };
}

function loadList() {
  return sanitizeList(readJSON(KEYS.lastIngredients, null));
}

// 1단계 캐시는 쓰기에 실패해도 알리지 않는다 — 다시 분석하면 그만이다(→ UNIT_storage.md).
function saveList() {
  writeJSON(KEYS.lastIngredients, list);
}

function userItem(name, quantity, category) {
  return { name, category, quantity: quantity || '미상', confidence: 'high', needs_review: false, source: 'user' };
}

/** 사람이 손댄 항목은 source=user가 되고 needs_review가 풀린다. */
function touch(item) {
  item.source = 'user';
  item.needs_review = false;
}

function flagged(item) {
  return item.source === 'vision' && (item.needs_review || item.confidence === 'low');
}

// ── 서버 상태 ────────────────────────────────────────────────────────────

async function checkHealth() {
  const el = $('health');
  try {
    const res = await fetch('/api/health', { cache: 'no-store' });
    const body = await res.json();
    if (body.ok) {
      el.dataset.state = 'ok';
      el.textContent = '준비됨';
    } else {
      el.dataset.state = 'error';
      el.textContent = body.error?.message || MSG_PROXY_DOWN;
    }
  } catch {
    el.dataset.state = 'error';
    el.textContent = MSG_PROXY_DOWN;
  }
}

// ── 이미지 받기 · 축소 ──────────────────────────────────────────────────

async function acceptFile(file, note = '') {
  const panel = panels.vision;
  if (panel.job || !file) return;        // 분석 중에는 사진을 바꾸지 않는다
  hideError(panel);
  // 확장자 없는 파일은 type이 비어 온다. 비었으면 실제로 열어 보고 판단한다.
  if (file.type && !file.type.startsWith('image/')) {
    showError(panel, '이미지 파일만 올릴 수 있습니다.', false);
    return;
  }
  setStatus(panel, 'idle', '사진을 줄이는 중…');
  try {
    photo = await shrink(file);
  } catch {
    photo = null;
    renderPreview();
    updateButtons();
    setStatus(panel, 'idle', MSG_IDLE);
    showError(panel, '이미지를 읽지 못했습니다. JPEG·PNG 같은 사진 파일을 올려 주세요.', false);
    return;
  }
  if (photo.dataUrl.length > MAX_DATA_URL) {
    photo = null;
    renderPreview();
    updateButtons();
    setStatus(panel, 'idle', MSG_IDLE);
    showError(panel, '줄인 뒤에도 사진이 4MB를 넘습니다. 다른 사진을 올려 주세요.', false);
    return;
  }
  renderPreview();
  updateButtons();
  const replace = list.items.length ? ' 분석하면 지금 재료 목록이 새 결과로 바뀝니다.' : '';
  setStatus(panel, 'idle', `${note}[재료 분석]을 누르세요. 보통 ${WAIT.vision.expect}초쯤 걸립니다.${replace}`);
}

/** 긴 변 768px · JPEG 0.85로 줄인다. 미리보기와 전송에 **같은** data URL을 쓴다. */
async function shrink(file) {
  // EXIF 회전을 반영해 연다. 휴대폰 세로 사진이 눕지 않게.
  const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
  try {
    const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height));
    const width = Math.max(1, Math.round(bitmap.width * scale));
    const height = Math.max(1, Math.round(bitmap.height * scale));
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#fff';               // 투명 PNG가 JPEG에서 검게 칠해지지 않게
    ctx.fillRect(0, 0, width, height);
    ctx.drawImage(bitmap, 0, 0, width, height);
    return {
      dataUrl: canvas.toDataURL('image/jpeg', JPEG_QUALITY),
      width, height, origWidth: bitmap.width, origHeight: bitmap.height,
    };
  } finally {
    bitmap.close();
  }
}

function renderPreview() {
  const fig = $('preview');
  if (!photo) {
    fig.hidden = true;
    $('preview-img').removeAttribute('src');
    return;
  }
  const kb = Math.round((photo.dataUrl.length * 3) / 4 / 1024);
  const resized = photo.origWidth !== photo.width
    ? ` (원본 ${photo.origWidth}×${photo.origHeight}에서 줄임)` : '';
  $('preview-img').src = photo.dataUrl;
  $('preview-cap').textContent = `모델이 받는 그림: ${photo.width}×${photo.height} JPEG, 약 ${kb}KB${resized}`;
  fig.hidden = false;
}

// ── 프록시 호출 — 두 단계가 함께 쓴다 (→ UNIT_ui_state.md) ────────────────

/**
 * 프록시에 POST하고, 기다리는 동안 경과 초를 보여준다.
 * 성공하면 응답 본문을, 실패·취소면 null을 돌려준다(오류 표시는 여기서 끝낸다).
 * panel.job은 첫 await 전에 잡히므로 버튼을 연달아 눌러도 요청은 한 번만 나간다.
 */
async function postWithWait(panel, wait, url, payload, loadingText) {
  const controller = new AbortController();
  panel.job = { controller, reason: null };
  hideError(panel);
  const started = Date.now();
  const giveUp = setTimeout(() => { panel.job.reason = 'timeout'; controller.abort(); }, wait.giveUp * 1000);

  // 낭독기에는 상태가 바뀔 때만 읽히게 하고, 매초 바뀌는 숫자는 따로 둔다.
  let slow = false;
  setStatus(panel, 'loading', loadingText);
  const tick = () => {
    const s = Math.floor((Date.now() - started) / 1000);
    $(panel.elapsed).textContent = `${s}초째`;
    if (!slow && s >= wait.slowAfter) {
      slow = true;
      setStatus(panel, 'loading', MSG_SLOW);
    }
  };
  tick();
  const timer = setInterval(tick, 1000);
  updateButtons();

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    let body = null;
    try { body = await res.json(); } catch { /* 프록시가 아닌 무언가가 답했다 */ }
    if (!body || typeof body !== 'object') {
      failed(panel, MSG_PROXY_DOWN, true);
      return null;
    }
    if (!body.ok) {
      failed(panel, body.error?.message || MSG_PROXY_DOWN, Boolean(body.error?.retryable));
      return null;
    }
    body.elapsedS = Math.round((body.meta?.elapsed_ms ?? Date.now() - started) / 1000);
    return body;
  } catch (err) {
    if (err?.name === 'AbortError' && panel.job.reason === 'timeout') {
      failed(panel, MSG_TIMEOUT, true);
    } else if (err?.name === 'AbortError') {
      setStatus(panel, 'idle', '취소했습니다. 이미 보낸 요청은 되돌릴 수 없어 사용량에는 잡힐 수 있습니다.');
    } else {
      failed(panel, MSG_PROXY_DOWN, true);
    }
    return null;
  } finally {
    clearInterval(timer);
    clearTimeout(giveUp);
    $(panel.elapsed).textContent = '';
    panel.job = null;
    updateButtons();
  }
}

// ── 1단계: 분석 ─────────────────────────────────────────────────────────

async function analyze() {
  const panel = panels.vision;
  if (!photo || panel.job) return;
  const body = await postWithWait(panel, WAIT.vision, '/api/vision', { image: photo.dataUrl },
    `분석 중입니다. 보통 ${WAIT.vision.expect}초쯤 걸립니다.`);
  if (!body) return;

  list = sanitizeList(body.data);
  saveList();
  render();
  const n = list.items.length;
  const review = list.items.filter(flagged).length;
  if (n) {
    setStatus(panel, 'done', `재료 ${n}개를 찾았습니다 (${body.elapsedS}초).`
      + (review ? ` 노란색으로 표시된 ${review}개는 확인해 주세요.` : ''));
  } else if (list.unsure.length) {
    setStatus(panel, 'done', `확실한 식재료는 찾지 못했습니다 (${body.elapsedS}초). 아래 "무엇인가요?" 목록을 확인해 주세요.`);
  } else {
    setStatus(panel, 'done', `사진에서 식재료를 찾지 못했습니다 (${body.elapsedS}초). 다른 사진을 올리거나 직접 추가하세요.`);
  }
}

// ── 2단계: 레시피 추천 ──────────────────────────────────────────────────

/**
 * 추천에 보낼 재료. 이름·수량만 보낸다(IngredientList 통째로 보내지 않는다 → PRD_step2 "API").
 * needs_review 항목은 사람이 고치기 전까지 보내지 않는다(→ UNIT_json_contract).
 * 이름은 프록시와 같은 방식(NFC·공백 정리)으로 맞춰야 uses 대조가 어긋나지 않는다.
 */
function recipeInputs() {
  const seen = new Set();
  const send = [];
  const skipped = [];
  for (const it of list.items) {
    const name = it.name.normalize('NFC').replace(/\s+/g, ' ').trim();
    if (!name) continue;
    if (it.needs_review) { skipped.push(name); continue; }
    if (seen.has(name)) continue;
    seen.add(name);
    send.push({ name, quantity: it.quantity });
  }
  return { send, skipped };
}

async function recommend() {
  const panel = panels.recipe;
  if (panel.job) return;
  const { send } = recipeInputs();
  if (!send.length) return;
  const body = await postWithWait(panel, WAIT.recipe, '/api/recipe', { ingredients: send, count: RECIPE_COUNT },
    `레시피를 고르는 중입니다. 보통 ${WAIT.recipe.expect}초쯤 걸립니다.`);
  if (!body) return;

  usedNames = send.map((i) => i.name);
  recipes = arrangeRecipes(body.data?.recipes, usedNames);
  renderRecipes();
  const ready = recipes.filter((r) => !r.missing.length).length;
  setStatus(panel, 'done', `레시피 ${recipes.length}개를 추천했습니다 (${body.elapsedS}초).`
    + (ready ? ` ${ready}개는 지금 바로 만들 수 있습니다.` : ''));
}

const strList = (v) => (Array.isArray(v)
  ? v.filter((x) => typeof x === 'string' && x.trim()).map((x) => x.trim())
  : []);

/**
 * 표시 규칙 (→ PRD_step2 "정렬과 표시").
 * 입력 재료에 없는 uses는 missing으로 옮기고(모델이 이름을 바꿔 쓴 경우),
 * missing이 적은 순 → time_minutes가 짧은 순으로 정렬한다.
 */
function arrangeRecipes(raw, names) {
  const have = new Set(names);
  const out = (Array.isArray(raw) ? raw : [])
    .filter((r) => r && typeof r === 'object' && typeof r.title === 'string')
    .map((r) => {
      const uses = [];
      const missing = [];
      for (const u of strList(r.uses)) (have.has(u) ? uses : missing).push(u);
      missing.push(...strList(r.missing));
      return { ...r, uses: [...new Set(uses)], missing: [...new Set(missing)], steps: strList(r.steps) };
    });
  const minutes = (r) => (Number.isFinite(r.time_minutes) ? r.time_minutes : Infinity);
  return out.sort((a, b) => a.missing.length - b.missing.length || minutes(a) - minutes(b));
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;   // 모델 출력은 항상 textContent로만 넣는다
  return node;
}

function chipRow(label, items, kind) {
  const wrap = el('div', `chips ${kind}`);
  const ul = el('ul');
  if (items.length) items.forEach((name) => ul.append(el('li', 'chip', name)));
  else ul.append(el('li', 'chip none', '없음'));
  wrap.append(el('span', 'chips-label', label), ul);
  return wrap;
}

function renderRecipes() {
  const box = $('recipes');
  box.replaceChildren();
  for (const r of recipes) {
    const card = el('article', 'recipe');
    card.dataset.id = String(r.id ?? '');
    const head = el('div', 'recipe-head');
    head.append(el('h3', null, r.title));
    if (!r.missing.length) head.append(el('span', 'badge-now', '지금 바로 가능'));
    card.append(head);

    const meta = [
      Number.isFinite(r.time_minutes) ? `${r.time_minutes}분` : null,
      r.difficulty || null,
      Number.isFinite(r.servings) ? `${r.servings}인분` : null,
    ].filter(Boolean).join(' · ');
    if (meta) card.append(el('p', 'recipe-meta', meta));
    if (r.summary) card.append(el('p', 'recipe-summary', r.summary));

    card.append(chipRow('가진 재료', r.uses, 'have'));
    card.append(chipRow('부족한 재료', r.missing, 'missing'));

    const ol = el('ol', 'recipe-steps');          // 번호는 화면이 붙인다(<ol>)
    r.steps.forEach((step) => ol.append(el('li', null, step)));
    card.append(ol);
    if (r.tips) card.append(el('p', 'recipe-tips', `팁: ${r.tips}`));
    box.append(card);
  }
  $('recipe-used').hidden = !recipes.length;
  $('recipe-used').textContent = `이 추천에 쓴 재료: ${usedNames.join(', ')}`;
}

function updateRecipeControls() {
  const { send, skipped } = recipeInputs();
  $('recommend').disabled = Boolean(panels.recipe.job) || !send.length;
  let hint = send.length
    ? `재료 ${send.length}개로 추천합니다. 재료를 고치고 다시 누르면 다른 요리가 나옵니다.`
    : '재료를 하나 이상 넣으면 추천받을 수 있습니다.';
  if (skipped.length) {
    hint += ` 확인 필요 ${skipped.length}개(${skipped.join(', ')})는 고치기 전까지 빼고 보냅니다.`;
  }
  $('recipe-hint').textContent = hint;
}

// ── 상태 표시 ───────────────────────────────────────────────────────────

function setStatus(panel, state, text) {
  const node = $(panel.status);
  node.dataset.state = state;
  node.textContent = text;
  node.hidden = !text;
}

function showError(panel, message, retryable) {
  $(panel.errorMsg).textContent = message;
  $(panel.error).dataset.retryable = String(retryable);
  $(panel.error).hidden = false;
  updateButtons();
}

function hideError(panel) {
  $(panel.error).hidden = true;
  $(panel.error).dataset.retryable = 'false';
}

function failed(panel, message, retryable) {
  setStatus(panel, 'error', '');
  showError(panel, message, retryable);
}

function updateButtons() {
  const vision = Boolean(panels.vision.job);
  $('analyze').disabled = vision || !photo;
  $('cancel').hidden = !vision;
  $('file').disabled = vision;
  $('drop').classList.toggle('disabled', vision);
  // 재시도는 retryable일 때만. NO_KEY에 달면 사용자는 영원히 누른다.
  $('retry').hidden = vision || !photo || $('error').dataset.retryable !== 'true';

  const recipe = Boolean(panels.recipe.job);
  $('recipe-cancel').hidden = !recipe;
  $('recipe-retry').hidden = recipe || $('recipe-error').dataset.retryable !== 'true';
  updateRecipeControls();
}

// ── 재료 목록 ───────────────────────────────────────────────────────────

function fillCategories(select, value) {
  select.replaceChildren(...CATEGORIES.map((c) => new Option(c, c, false, c === value)));
}

function render() {
  const groups = $('groups');
  groups.replaceChildren();
  for (const cat of CATEGORIES) {
    const rows = [];
    list.items.forEach((it, i) => { if (it.category === cat) rows.push(itemRow(it, i)); });
    if (!rows.length) continue;
    const section = document.createElement('section');
    section.className = 'group';
    const h = document.createElement('h3');
    h.textContent = `${cat} (${rows.length})`;
    const ul = document.createElement('ul');
    ul.append(...rows);
    section.append(h, ul);
    groups.append(section);
  }
  $('empty').hidden = list.items.length > 0;
  renderUnsure();
  renderSummary();
  updateRecipeControls();
}

function itemRow(item, index) {
  const li = $('item-tpl').content.firstElementChild.cloneNode(true);
  li.dataset.index = String(index);
  const name = li.querySelector('.i-name');
  const qty = li.querySelector('.i-qty');
  const cat = li.querySelector('.i-cat');
  const badge = li.querySelector('.badge');
  name.value = item.name;
  qty.value = item.quantity;
  fillCategories(cat, item.category);
  paintReview(li, item);

  // 이름·수량은 그 자리에서만 갱신한다(전체를 다시 그리면 Tab 이동 중 포커스를 잃는다).
  name.addEventListener('change', () => {
    const v = name.value.trim();
    if (!v) { name.value = item.name; return; }   // 빈 이름으로는 바꾸지 않는다. 지우려면 삭제.
    item.name = v;
    touch(item);
    commitInPlace(li, item);
  });
  qty.addEventListener('change', () => {
    item.quantity = qty.value.trim() || '미상';
    qty.value = item.quantity;
    touch(item);
    commitInPlace(li, item);
  });
  // 카테고리가 바뀌면 그룹이 옮겨지므로 다시 그린다.
  cat.addEventListener('change', () => {
    item.category = cat.value;
    touch(item);
    saveList();
    render();
    document.querySelector(`.item[data-index="${index}"] .i-cat`)?.focus();
  });
  // 모델이 맞게 읽었는데 표시가 붙은 경우: 고칠 것이 없으니 확인만 누른다.
  badge.addEventListener('click', () => {
    touch(item);
    commitInPlace(li, item);
    name.focus();
  });
  li.querySelector('.i-del').addEventListener('click', () => {
    list.items.splice(index, 1);
    saveList();
    render();
    $('list-title').focus();
  });
  return li;
}

function commitInPlace(li, item) {
  paintReview(li, item);
  saveList();
  renderSummary();
  updateRecipeControls();
}

function paintReview(li, item) {
  const on = flagged(item);
  const badge = li.querySelector('.badge');
  li.classList.toggle('review', on);
  badge.hidden = !on;
  badge.textContent = item.needs_review ? '확인 필요' : '확신 낮음';
  badge.title = '이대로 맞으면 눌러서 확인 표시를 푸세요';
}

function renderUnsure() {
  const ul = $('unsure-list');
  ul.replaceChildren();
  $('unsure-box').hidden = list.unsure.length === 0;
  list.unsure.forEach((text, index) => {
    const li = document.createElement('li');
    li.className = 'unsure-item';
    const p = document.createElement('p');
    p.textContent = text;

    const form = document.createElement('form');
    form.className = 'row';
    const name = Object.assign(document.createElement('input'), {
      type: 'text', maxLength: 40, required: true, placeholder: '이름을 붙이면 재료로 올립니다',
    });
    name.setAttribute('aria-label', `"${text}"의 이름`);
    const cat = document.createElement('select');
    cat.setAttribute('aria-label', '카테고리');
    fillCategories(cat, '기타');
    const add = Object.assign(document.createElement('button'), { type: 'submit', textContent: '재료로 추가' });
    const skip = Object.assign(document.createElement('button'), { type: 'button', textContent: '무시' });

    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const v = name.value.trim();
      if (!v) return;
      list.items.push(userItem(v, '미상', cat.value));
      list.unsure.splice(index, 1);
      saveList();
      render();
    });
    skip.addEventListener('click', () => {
      list.unsure.splice(index, 1);
      saveList();
      render();
    });

    form.append(name, cat, add, skip);
    li.append(p, form);
    ul.append(li);
  });
}

function renderSummary() {
  const n = list.items.length;
  if (!n) { $('summary').textContent = ''; return; }
  const review = list.items.filter(flagged).length;
  let when = '직접 입력';
  if (list.analyzed_at) {
    const d = new Date(list.analyzed_at);
    if (!Number.isNaN(d.getTime())) {
      when = `${d.toLocaleString('ko-KR', { dateStyle: 'short', timeStyle: 'short' })} 분석`;
    }
  }
  $('summary').textContent = `재료 ${n}개${review ? ` · 확인 필요 ${review}개` : ''} · ${when}`;
}

// ── 이벤트 연결 ─────────────────────────────────────────────────────────

function wire() {
  const input = $('file');
  input.addEventListener('change', () => {
    acceptFile(input.files[0]);
    input.value = '';                       // 같은 파일을 다시 골라도 change가 오게
  });

  // 드롭은 어디에 떨어뜨려도 받는다. 막지 않으면 브라우저가 사진 파일로 이동해 앱이 사라진다.
  const drop = $('drop');
  window.addEventListener('dragover', (e) => {
    e.preventDefault();
    drop.classList.add('over');
  });
  window.addEventListener('dragleave', (e) => {
    if (!e.relatedTarget) drop.classList.remove('over');
  });
  window.addEventListener('drop', (e) => {
    e.preventDefault();
    drop.classList.remove('over');
    const files = e.dataTransfer?.files;
    if (!files?.length) return;
    // 여러 장 동시 분석은 하지 않는다(→ PRD_step1.md "하지 않는 것").
    acceptFile(files[0], files.length > 1 ? '여러 장 중 첫 번째 사진만 씁니다. ' : '');
  });

  // 붙여넣기: 스크린샷을 바로 넣는다. 글자 붙여넣기는 방해하지 않는다.
  document.addEventListener('paste', (e) => {
    const entry = [...(e.clipboardData?.items ?? [])].find((it) => it.kind === 'file');
    if (!entry) return;
    e.preventDefault();
    acceptFile(entry.getAsFile());
  });

  $('analyze').addEventListener('click', analyze);
  $('retry').addEventListener('click', analyze);
  $('cancel').addEventListener('click', () => panels.vision.job?.controller.abort());

  $('recommend').addEventListener('click', recommend);
  $('recipe-retry').addEventListener('click', recommend);
  $('recipe-cancel').addEventListener('click', () => panels.recipe.job?.controller.abort());

  const form = $('add-form');
  fillCategories(form.elements.category, '기타');
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const name = form.elements.name.value.trim();
    if (!name) return;
    list.items.push(userItem(name, form.elements.quantity.value.trim(), form.elements.category.value));
    saveList();
    render();
    form.reset();
    fillCategories(form.elements.category, '기타');
    form.elements.name.focus();
  });
}

wire();
render();
updateButtons();
checkHealth();
