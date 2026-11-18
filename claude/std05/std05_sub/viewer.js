// 한눈 PDF 요약 — 추출한 텍스트 보기 창 (SPEC §10.8, ES 모듈 하나, 빌드 없음)
//
// 흐름: 주소의 #doc=TOKEN → BroadcastChannel 'pdf-summary-viewer'로 원래 창에 문서(File)를 달라고 한다
//       → POST /api/layout?from=&to=(&body=) 를 5쪽씩 한 번에 하나씩 끝까지 → 원본 배치·읽기 모드로 그린다.
// 규칙: 문서·서버 문자열은 모두 textContent로만(innerHTML 금지). 그림은 data:image/png|jpeg;base64 만.
//       문서는 이 창의 메모리에만 둔다(localStorage·sessionStorage·IndexedDB에 넣지 않는다).
//       오류 문구는 서버 message를 쓰지 않고 code로 여기서 정한다(SPEC §10.7 표 — app.js와 같은 표).

const CHANNEL = 'pdf-summary-viewer';
const BATCH = 5;                       // 보기 창은 5쪽씩 요청(SPEC §10.2)
const NO_DOC_GRACE_MS = 500;           // no-doc을 받아도 다른 탭이 문서를 줄 수 있어 잠깐 더 기다린다
const PT2PX = 96 / 72;
const WIDE_DEFAULT_PX = 700;           // 이 폭 이상이면 원본 배치로 시작(미결 U18 기본값)
// 시험 스크립트만 globalThis.__pdfViewerTimeouts로 줄인다.
const TIMEOUTS = Object.assign({ connect: 3_000, layout: 90_000, export: 200_000 },
  globalThis.__pdfViewerTimeouts || {});
const DATA_URI = /^data:image\/(?:png|jpeg);base64,[A-Za-z0-9+/]+={0,2}$/;
const BLOCK_ID = /^p\d{1,4}-b\d{1,6}$/;

// ── 문구 ────────────────────────────────────────────────
const MSG = {
  TOOL_MISSING: ['표·그림을 읽는 프로그램이 없어요. 관리자에게 ', { code: 'sudo apt install poppler-utils' }, ' 설치를 요청하세요.'],
  BUSY: ['다른 창에서 문서를 읽고 있어요. 잠시 뒤 다시 눌러 주세요.'],
  EXTRACT_TIMEOUT: ['문서가 복잡해 시간이 너무 걸려 멈췄어요. 잠시 뒤 다시 시도해 주세요.'],
  CLIENT_TIMEOUT: ['문서가 복잡해 시간이 너무 걸려 멈췄어요. 잠시 뒤 다시 시도해 주세요.'],
  ENCRYPTED: ['암호가 걸린 PDF예요. 암호를 푼 뒤 다시 저장해서 올려 주세요.'],
  BROKEN_PDF: ['PDF를 열 수 없어요. 파일이 손상됐을 수 있어요. 다시 내려받거나 다시 저장해 보세요.'],
  NOT_PDF: ['PDF 파일만 요약할 수 있어요. 확장자가 .pdf인 파일을 골라 주세요.'],
  EMPTY_FILE: ['빈 파일이에요. 내용이 있는 PDF를 골라 주세요.'],
  TOO_LONG: ['문서가 너무 길어요(300쪽까지). 필요한 부분만 따로 저장해 올려 주세요.'],
  FILE_TOO_LARGE: ['파일이 너무 커요(20MB까지). 쪽을 나눠 저장해 올려 주세요.'],
  NETWORK: ['서버에 연결할 수 없어요. ', { code: './run.sh' }, '가 실행 중인지 확인해 주세요.'],
  UNKNOWN: ['알 수 없는 문제가 생겼어요. 다시 시도해 주세요.'],
  NO_SOURCE: ['원래 창(한눈 PDF 요약)이 닫혔거나 다른 파일을 열어 이 문서를 다시 받을 수 없어요. 원래 창에서 [추출한 텍스트 보기]를 다시 눌러 주세요.'],
};
const OMITTED = { LIMIT: '파일 크기 제한', TIME: '시간 제한', FAILED: '불러오지 못함' };
const MODE_HELP = {
  original: '원본 배치: 원본 쪽 모양대로 놓았어요. 여러 줄을 골라 복사하면 순서가 어긋날 수 있어요 — 정확히 복사하려면 읽기 모드를 쓰세요.',
  reading: '읽기 모드: 글을 위에서 아래로 이어 놓았어요. 복사할 때는 이 모드가 정확해요.',
};

// ── DOM ─────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const el = {
  boot: $('boot-notice'), title: $('doc-title'), progress: $('progress-text'), announcer: $('announcer'),
  bar: $('vbar'), modeOriginal: $('mode-original'), modeReading: $('mode-reading'), tocToggle: $('toc-toggle'),
  pageInput: $('page-input'), pageTotal: $('page-total'), print: $('print-button'), printHint: $('print-hint'),
  download: $('download-button'), downloadStatus: $('download-status'), downloadError: $('download-error'),
  modeHelp: $('mode-help'),
  toc: $('toc'), tocList: $('toc-list'), tocEmpty: $('toc-empty'),
  pages: $('pages'), pageList: $('page-list'),
  stateCard: $('state-card'), stateSpinner: $('state-spinner'), stateTitle: $('state-title'), stateText: $('state-text'),
  reconnect: $('reconnect-button'), restart: $('restart-button'),
};

// ── 상태 ────────────────────────────────────────────────
const state = {
  token: '',
  run: 0,              // 다시 연결·다시 시도마다 1씩 — 늦게 온 이전 응답을 버린다
  source: null,        // { blob, fileName, title, kind: 'file'|'bytes' } — 이 창의 메모리에만
  doc: null,           // 첫 응답의 doc { pages, title, page_sizes, outline, outline_source }
  bodySize: null,
  pages: [],           // [{ status: 'pending'|'loading'|'done'|'error', data, code, section }]
  priority: 0,         // 목차·쪽 이동으로 먼저 불러올 쪽
  loading: null,       // { run, controller, from, to }
  mode: 'original',
  current: 1,
  target: null,        // 아직 안 불러온 곳으로 이동을 눌렀을 때 { page, block } — 불러오면 그리로
  tocOpen: false,
  announcedDone: false,
  download: null,
};

// ── 작은 도구 ───────────────────────────────────────────
function make(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function fillMessage(target, parts) {
  target.replaceChildren();
  const p = document.createElement('p');
  for (const part of parts) {
    if (typeof part === 'string') p.append(part);
    else p.append(make('code', '', part.code));
  }
  target.append(p);
}

function announce(text) {
  el.announcer.textContent = '';
  setTimeout(() => { el.announcer.textContent = text; }, 50);
}

const nf = new Intl.NumberFormat('ko-KR');
const num = (n) => nf.format(n);
const msgOf = (code) => MSG[Object.prototype.hasOwnProperty.call(MSG, code) ? code : 'UNKNOWN'];
const fin = (v, d = 0) => (typeof v === 'number' && Number.isFinite(v) ? v : d);
const str = (v) => (typeof v === 'string' ? v : '');
const batchStart = (p) => Math.floor((p - 1) / BATCH) * BATCH + 1;

// 내려받을 파일 이름(SPEC §10.6 — app.js·서버 pdf_layout.export_file_name과 같은 규칙)
function exportFileName(name) {
  let s = String(name || '').replace(/\.pdf$/i, '');
  s = s.replace(/[\\/:*?"<>|\u0000-\u001f\u007f-\u009f]/g, '_');
  s = s.replace(/^[\s.]+|[\s.]+$/g, '');
  s = Array.from(s).slice(0, 100).join('');
  return s ? `${s}_추출.html` : '추출한_텍스트.html';
}

// ── 안내 카드 (받는 중 · NO_SOURCE · 첫 묶음 오류) ───────
function showState(kind, code = null) {
  el.stateCard.hidden = false;
  el.stateCard.classList.toggle('is-error', kind !== 'connecting' && kind !== 'loading');
  el.stateSpinner.hidden = !(kind === 'connecting' || kind === 'loading');
  el.reconnect.hidden = kind !== 'no-source';
  el.restart.hidden = kind !== 'error';
  if (kind === 'connecting') {
    el.stateTitle.textContent = '원래 창에서 문서를 받고 있어요…';
    el.stateText.replaceChildren();
  } else if (kind === 'loading') {
    el.stateTitle.textContent = '쪽 구조를 읽고 있어요…';
    el.stateText.replaceChildren();
  } else if (kind === 'no-source') {
    el.stateTitle.textContent = '문서를 다시 받을 수 없어요';
    fillMessage(el.stateText, MSG.NO_SOURCE);
    el.title.textContent = '문서를 받지 못했어요';
    announce('문서를 다시 받을 수 없어요. 원래 창에서 추출한 텍스트 보기를 다시 눌러 주세요.');
    el.reconnect.focus();
  } else {
    el.stateTitle.textContent = '문서를 불러오지 못했어요';
    fillMessage(el.stateText, msgOf(code));
    announce(`문서를 불러오지 못했어요. ${el.stateText.textContent}`);
    el.restart.focus();
  }
}

// ── 1. 문서 받기 (BroadcastChannel) ─────────────────────
function connect() {
  const run = ++state.run;
  showState('connecting');
  if (!state.token || typeof BroadcastChannel !== 'function') {
    showState('no-source');
    return;
  }
  const ch = new BroadcastChannel(CHANNEL);
  let done = false;
  let graceTimer = 0;
  const finish = () => { done = true; clearTimeout(waitTimer); clearTimeout(graceTimer); ch.close(); };
  const fail = () => { if (done || run !== state.run) return; finish(); showState('no-source'); };
  const waitTimer = setTimeout(fail, TIMEOUTS.connect);
  ch.onmessage = async (e) => {
    const m = e.data;
    if (done || run !== state.run || !m || typeof m !== 'object' || m.token !== state.token) return;
    if (m.type === 'no-doc') {
      if (!graceTimer) graceTimer = setTimeout(fail, NO_DOC_GRACE_MS);
      return;
    }
    if (m.type !== 'doc') return;
    // File(Blob)로 오면 그대로, ArrayBuffer(bytes)로 오면 Blob으로 감싼다(SPEC §10.7 대안).
    let blob = null;
    let kind = '';
    if (m.file instanceof Blob) { blob = m.file; kind = 'file'; }
    else if (m.bytes instanceof ArrayBuffer) { blob = new Blob([m.bytes], { type: 'application/pdf' }); kind = 'bytes'; }
    if (!blob || !blob.size) return;
    finish();
    // 원래 창이 닫히거나 디스크의 파일이 바뀌어도 이 창이 계속 동작하도록 바이트를 메모리로 옮겨 둔다.
    try {
      const bytes = await blob.arrayBuffer();
      blob = new Blob([bytes], { type: 'application/pdf' });
    } catch {
      if (run === state.run) showState('no-source');
      return;
    }
    if (run !== state.run) return;
    state.source = { blob, fileName: str(m.fileName), title: str(m.title).trim(), kind };
    document.documentElement.dataset.source = kind;      // 시험이 전달 방식을 확인한다(문서 내용 아님)
    setTitle(state.source.title);
    startLoading();
  };
  ch.postMessage({ type: 'need-doc', token: state.token });
}

function setTitle(t) {
  const title = t || state.source?.fileName || '제목 없는 문서';
  el.title.textContent = title;
  document.title = `${title} — 추출한 텍스트 보기`;
}

// ── 2. 불러오기 ─────────────────────────────────────────
function startLoading() {
  state.doc = null;
  state.bodySize = null;
  state.pages = [];
  state.announcedDone = false;
  el.pageList.replaceChildren();
  showState('loading');
  announce('문서를 불러오고 있어요.');
  load(1, BATCH);
}

function nextRange() {
  const n = state.pages.length;
  const pending = (p) => p >= 1 && p <= n && state.pages[p - 1].status === 'pending';
  let from = 0;
  if (state.priority && pending(state.priority)) {
    const bs = batchStart(state.priority);
    from = state.priority;
    while (from - 1 >= bs && pending(from - 1)) from -= 1;
  } else {
    state.priority = 0;
    for (let p = 1; p <= n; p += 1) if (pending(p)) { from = p; break; }
  }
  if (!from) return null;
  const end = Math.min(batchStart(from) + BATCH - 1, n);
  let to = from;
  while (to + 1 <= end && pending(to + 1)) to += 1;
  return [from, to];
}

function pump() {
  if (state.loading || !state.doc) return;
  const r = nextRange();
  if (!r) { updateProgress(); return; }
  load(r[0], r[1]);
}

async function load(from, to) {
  const run = state.run;
  const L = { run, controller: new AbortController(), from, to, timedOut: false };
  const timer = setTimeout(() => { L.timedOut = true; L.controller.abort(); }, TIMEOUTS.layout);
  state.loading = L;
  for (let p = from; p <= Math.min(to, state.pages.length); p += 1) setPageStatus(p, 'loading');
  let url = `/api/layout?from=${from}&to=${to}`;
  if (state.bodySize) url += `&body=${encodeURIComponent(state.bodySize)}`;
  let body = null;
  let code = null;
  try {
    const res = await fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/pdf' }, body: state.source.blob, signal: L.controller.signal,
    });
    try { body = await res.json(); } catch { body = null; }
    if (!res.ok) {
      const c = body && body.error && typeof body.error.code === 'string' ? body.error.code : '';
      code = c || (res.status === 409 ? 'BUSY' : res.status === 504 ? 'EXTRACT_TIMEOUT' : 'UNKNOWN');
      body = null;
    } else if (!body || typeof body !== 'object') {
      code = L.timedOut ? 'CLIENT_TIMEOUT' : 'UNKNOWN';
    }
  } catch {
    code = L.timedOut ? 'CLIENT_TIMEOUT' : 'NETWORK';
  } finally {
    clearTimeout(timer);
  }
  if (run !== state.run || state.loading !== L) return;     // 다시 연결·다시 시도로 버려진 응답
  state.loading = null;

  if (!code && !state.doc) {
    const doc = normalizeDoc(body.doc);
    if (!doc) code = 'UNKNOWN';
    else setupDoc(doc, body);
  }
  if (code) {
    if (!state.doc) { showState('error', code); return; }
    for (let p = from; p <= Math.min(to, state.pages.length); p += 1) {
      if (state.pages[p - 1].status === 'loading') setPageStatus(p, 'error', code);
    }
    renderBatchErrors();
    updateProgress();
    pump();
    return;
  }
  const got = new Set();
  const list = Array.isArray(body.pages) ? body.pages : [];
  const fresh = [];
  for (const raw of list) {
    const page = normalizePage(raw);
    if (!page || page.number < from || page.number > to || page.number > state.pages.length) continue;
    const slot = state.pages[page.number - 1];
    if (slot.status === 'done') continue;
    slot.data = page;
    slot.status = 'done';
    slot.code = null;
    got.add(page.number);
    fresh.push(page.number);
  }
  // 서버가 범위를 줄여 준 경우(응답 to < 요청 to) 남은 쪽은 다음 차례로, 받은 것이 하나도 없으면 오류.
  for (let p = from; p <= Math.min(to, state.pages.length); p += 1) {
    if (!got.has(p) && state.pages[p - 1].status === 'loading') {
      setPageStatus(p, got.size ? 'pending' : 'error', got.size ? null : 'UNKNOWN');
    }
  }
  renderPages(fresh);
  renderBatchErrors();
  if (state.doc.outline_source !== 'bookmarks') buildToc();
  updateProgress();
  goToTargetIfReady();
  pump();
}

// 서버 JSON을 계약 모양으로 정리(SPEC §10.4). 틀린 값은 버리거나 기본값으로.
function normalizeDoc(d) {
  if (!d || typeof d !== 'object') return null;
  const pages = Math.floor(fin(d.pages));
  if (pages < 1 || pages > 2000) return null;
  const sizes = [];
  for (let i = 0; i < pages; i += 1) {
    const s = Array.isArray(d.page_sizes) ? d.page_sizes[i] : null;
    const w = Array.isArray(s) ? fin(s[0]) : 0;
    const h = Array.isArray(s) ? fin(s[1]) : 0;
    sizes.push(w > 0 && h > 0 ? [w, h] : [612, 792]);
  }
  const outline = (Array.isArray(d.outline) ? d.outline : []).map((o) => (o && typeof o === 'object' ? {
    title: str(o.title).trim(), page: Math.floor(fin(o.page)), level: Math.min(6, Math.max(1, Math.floor(fin(o.level, 1)))),
  } : null)).filter((o) => o && o.title && o.page >= 1 && o.page <= pages);
  return {
    pages, title: str(d.title).trim(), page_sizes: sizes, outline,
    outline_source: d.outline_source === 'bookmarks' && outline.length ? 'bookmarks' : 'none',
  };
}

function normalizePage(p) {
  if (!p || typeof p !== 'object') return null;
  const number = Math.floor(fin(p.number));
  if (number < 1) return null;
  const size = state.doc.page_sizes[number - 1] || [612, 792];
  const width = fin(p.width) > 0 ? p.width : size[0];
  const height = fin(p.height) > 0 ? p.height : size[1];
  const blocks = [];
  let i = 0;
  for (const b of Array.isArray(p.blocks) ? p.blocks : []) {
    if (!b || typeof b !== 'object') continue;
    const id = typeof b.id === 'string' && BLOCK_ID.test(b.id) ? b.id : `p${number}-b${i}`;
    i += 1;
    const box = { id, x: fin(b.x), y: fin(b.y), w: Math.max(0, fin(b.w)), h: Math.max(0, fin(b.h)) };
    if (b.type === 'text') {
      const role = ['h1', 'h2', 'h3', 'p'].includes(b.role) ? b.role : 'p';
      const lines = (Array.isArray(b.lines) ? b.lines : []).filter((l) => l && typeof l === 'object').map((l) => ({
        x: fin(l.x), y: fin(l.y), w: Math.max(0, fin(l.w)), h: Math.max(0, fin(l.h)),
        size: Math.min(200, Math.max(1, fin(l.size, 12))), bold: l.bold === true, text: str(l.text),
      }));
      const text = str(b.text) || lines.map((l) => l.text).join(' ');
      if (!text.trim()) continue;
      blocks.push({ ...box, type: 'text', role, text, lines });
    } else if (b.type === 'table') {
      const rows = (Array.isArray(b.rows) ? b.rows : []).filter(Array.isArray)
        .map((r) => r.map((c) => ({ text: str(c && c.text), bold: !!(c && c.bold === true) })));
      if (!rows.length) continue;
      const ncol = Math.max(...rows.map((r) => r.length));
      for (const r of rows) while (r.length < ncol) r.push({ text: '', bold: false });
      const cols = (Array.isArray(b.cols) ? b.cols : []).map((c) => fin(c, NaN)).filter(Number.isFinite);
      blocks.push({ ...box, type: 'table', rows, cols: cols.length === ncol ? cols : [], size: fin(b.size, 0) });
    } else if (b.type === 'figure') {
      const src = typeof b.src === 'string' && DATA_URI.test(b.src) ? b.src : null;
      let omitted = Object.prototype.hasOwnProperty.call(OMITTED, b.omitted) ? b.omitted : null;
      if (!src && !omitted) omitted = 'FAILED';       // 형식이 틀린 src(javascript:·http: 등)는 그리지 않는다
      blocks.push({ ...box, type: 'figure', src: omitted ? null : src, omitted, text: str(b.text).trim() });
    }
  }
  return { number, width, height, blocks, figures_failed: p.figures_failed === true };
}

function setupDoc(doc, body) {
  state.doc = doc;
  state.bodySize = fin(body.body_size) >= 4 && fin(body.body_size) <= 72 ? body.body_size : null;
  if (!state.source.title && doc.title) setTitle(doc.title);
  state.pages = doc.page_sizes.map(() => ({ status: 'pending', data: null, code: null, section: null }));
  for (let p = 1; p <= Math.min(BATCH, doc.pages); p += 1) state.pages[p - 1].status = 'loading';
  // 모든 쪽의 자리를 미리 만든다(같은 비율의 회색 상자) — 목차로 먼 쪽에 바로 갈 수 있게.
  const frag = document.createDocumentFragment();
  for (let p = 1; p <= doc.pages; p += 1) {
    const sec = make('section', 'pg');
    sec.id = `p${p}`;
    sec.dataset.page = String(p);
    sec.tabIndex = -1;
    sec.setAttribute('aria-label', `${p}쪽`);
    const no = make('p', 'pg__no', `${p}쪽`);
    no.setAttribute('aria-hidden', 'true');
    sec.append(no, make('div', 'pg__box'));
    state.pages[p - 1].section = sec;
    renderPlaceholder(p);
    frag.append(sec);
  }
  el.pageList.replaceChildren(frag);
  el.stateCard.hidden = true;
  el.bar.hidden = false;
  el.toc.hidden = !(isWide() || state.tocOpen);
  el.pageInput.max = String(doc.pages);
  el.pageTotal.textContent = `/ ${num(doc.pages)}`;
  setMode(window.innerWidth >= WIDE_DEFAULT_PX ? 'original' : 'reading', false);
  buildToc();
  measureBar();
  updateCurrent();
}

function setPageStatus(p, status, code = null) {
  const slot = state.pages[p - 1];
  if (!slot) return;
  slot.status = status;
  slot.code = code;
  if (status !== 'done' && slot.section) renderPlaceholder(p);
}

// ── 3. 그리기 ───────────────────────────────────────────
function boxOf(p) { return state.pages[p - 1].section.querySelector('.pg__box'); }

function sizeBox(box, w, h) {
  box.style.maxWidth = `${Math.round(w * PT2PX)}px`;
  box.dataset.ratio = `${w} / ${h}`;
}

function renderPlaceholder(p) {
  const slot = state.pages[p - 1];
  const [w, h] = state.doc.page_sizes[p - 1];
  const box = boxOf(p);
  sizeBox(box, w, h);
  const ph = make('div', 'ph');
  ph.style.aspectRatio = `${w} / ${h}`;
  if (slot.status === 'error') {
    ph.classList.add('is-error');
    ph.append(make('p', '', `${p}쪽을 불러오지 못했어요.`));
  } else {
    ph.append(make('p', '', `${p}쪽 불러오는 중`));
  }
  box.replaceChildren(ph);
  const note = slot.section.querySelector('.pg__note');
  if (note) note.remove();
}

// 실패한 묶음: 묶음의 첫 쪽 자리에 문구 + [다시 시도] 하나.
function renderBatchErrors() {
  const seen = new Set();
  state.pages.forEach((slot, i) => {
    if (slot.status !== 'error') return;
    const p = i + 1;
    const bs = batchStart(p);
    const ph = boxOf(p).querySelector('.ph');
    if (!ph || seen.has(bs)) return;
    seen.add(bs);
    if (ph.querySelector('button')) return;
    const msg = make('div', 'ph__msg');
    fillMessage(msg, msgOf(slot.code));
    const btn = make('button', 'vbtn vbtn--primary', '다시 시도');
    btn.type = 'button';
    btn.dataset.action = 'retry-batch';
    btn.dataset.page = String(p);
    btn.setAttribute('aria-label', `${p}쪽부터 다시 불러오기`);
    ph.append(msg, btn);
  });
}

function renderPages(list) {
  const lines = [];
  for (const p of list) lines.push(...renderPage(p));
  fitLines(lines);
}

// 쪽 하나를 지금 모드로 그린다. 원본 배치면 폭을 맞춰야 할 줄 목록을 돌려준다.
function renderPage(p) {
  const slot = state.pages[p - 1];
  const page = slot.data;
  const box = boxOf(p);
  sizeBox(box, page.width, page.height);
  const out = [];
  if (state.mode === 'original') box.replaceChildren(drawOriginal(page, out));
  else box.replaceChildren(drawFlow(page));
  let note = slot.section.querySelector('.pg__note');
  if (page.figures_failed) {
    if (!note) { note = make('p', 'pg__note'); slot.section.append(note); }
    note.textContent = '이 쪽의 그림은 불러오지 못했어요.';
  } else if (note) note.remove();
  return out;
}

const headingTag = { h1: 'h2', h2: 'h3', h3: 'h4', p: 'p' };   // 창 제목이 h1이라 한 단계씩 내린다

function figureNodes(b, n, k, original) {
  const fig = make('figure', 'fig');
  fig.id = b.id;
  if (b.src) {
    const img = document.createElement('img');
    img.alt = `${n}쪽 그림 ${k}`;
    img.width = Math.max(1, Math.round(b.w * PT2PX));
    img.height = Math.max(1, Math.round(b.h * PT2PX));
    img.decoding = 'async';
    img.src = b.src;
    fig.append(img);
  } else {
    const o = make(original ? 'div' : 'p', 'omitted', `[그림 생략: ${OMITTED[b.omitted] || OMITTED.FAILED}]`);
    o.setAttribute('role', 'img');
    o.setAttribute('aria-label', `${n}쪽 그림 ${k} — 생략: ${OMITTED[b.omitted] || OMITTED.FAILED}`);
    fig.append(o);
  }
  if (b.text) {
    // 원본 배치에서는 그림 속에 이미 보이므로 화면 읽기 프로그램에만(겹쳐 보이지 않게)
    fig.append(make('figcaption', original ? 'visually-hidden' : '', b.text));
  }
  return fig;
}

function tableNode(b) {
  const table = document.createElement('table');
  const head = b.rows.length >= 2 && b.rows[0].every((c) => c.bold);
  const tbody = document.createElement('tbody');
  b.rows.forEach((row, r) => {
    const tr = document.createElement('tr');
    for (const c of row) {
      const cell = make(head && r === 0 ? 'th' : 'td', '', c.text);
      if (head && r === 0) cell.scope = 'col';
      else if (c.bold) cell.classList.add('is-bold');
      tr.append(cell);
    }
    tbody.append(tr);
  });
  table.append(tbody);
  return table;
}

function drawOriginal(page, lines) {
  const W = page.width;
  const u = (v) => `${((v / W) * 100).toFixed(4)}cqw`;    // pt → 종이 폭 기준 단위
  const paper = make('div', 'paper');
  paper.style.aspectRatio = `${W} / ${page.height}`;
  let k = 0;
  for (const b of page.blocks) {
    if (b.type === 'text') {
      const blk = make(headingTag[b.role], 'blk');
      blk.id = b.id;
      Object.assign(blk.style, { left: u(b.x), top: u(b.y), width: u(b.w), height: u(b.h) });
      const src = b.lines.length ? b.lines : [{ x: b.x, y: b.y, w: b.w, h: b.h, size: b.h / 1.15 || 12, bold: false, text: b.text }];
      for (const l of src) {
        const ln = make('span', `ln${l.bold ? ' is-bold' : ''}`, l.text);
        Object.assign(ln.style, { left: u(l.x - b.x), top: u(l.y - b.y), fontSize: u(l.size) });
        ln.dataset.w = String(l.w / W);
        blk.append(ln);
        lines.push(ln);
      }
      paper.append(blk);
    } else if (b.type === 'table') {
      const wrap = make('div', 'tblbox');
      Object.assign(wrap.style, { left: u(b.x), top: u(b.y), width: u(b.w), height: u(b.h) });
      const size = b.size || (b.h / Math.max(1, b.rows.length)) / 1.3 || 10;
      wrap.style.fontSize = u(size);
      const table = tableNode(b);
      table.id = b.id;
      if (b.cols.length) {
        const cg = document.createElement('colgroup');
        b.cols.forEach((x, i) => {
          const next = i + 1 < b.cols.length ? b.cols[i + 1] : b.x + b.w;
          const col = document.createElement('col');
          col.style.width = `${Math.max(1, ((next - x) / Math.max(1, b.w)) * 100).toFixed(3)}%`;
          cg.append(col);
        });
        table.prepend(cg);
      }
      wrap.append(table);
      paper.append(wrap);
    } else {
      k += 1;
      const fig = figureNodes(b, page.number, k, true);
      Object.assign(fig.style, { left: u(b.x), top: u(b.y), width: u(b.w), height: u(b.h) });
      paper.append(fig);
    }
  }
  return paper;
}

function drawFlow(page) {
  const flow = make('div', 'flow');
  let k = 0;
  for (const b of page.blocks) {
    if (b.type === 'text') {
      const e = make(headingTag[b.role], '', b.text);
      e.id = b.id;
      flow.append(e);
    } else if (b.type === 'table') {
      const wrap = make('div', 'tblscroll');
      const table = tableNode(b);
      table.id = b.id;
      wrap.append(table);
      flow.append(wrap);
    } else {
      k += 1;
      flow.append(figureNodes(b, page.number, k, false));
    }
  }
  if (!page.blocks.length) flow.append(make('p', 'omitted', '이 쪽에는 글자·표·그림이 없어요.'));
  return flow;
}

// 원본 배치: 그린 줄의 실제 폭을 재어 원본 줄 폭에 맞게 가로로 늘이고 줄인다(0.5~2배).
// 위치·글자 크기가 모두 종이 폭에 비례하므로 비율은 화면 폭이 바뀌어도 거의 그대로다.
// 읽기를 몰아서 한 뒤 쓰기를 몰아서 한다(레이아웃을 한 번만 계산하게).
function fitLines(lines) {
  if (!lines.length) return;
  const reads = lines.map((ln) => {
    const paper = ln.closest('.paper');
    return [ln, ln.offsetWidth, paper ? paper.clientWidth : 0];
  });
  for (const [ln, natural, pw] of reads) {
    const want = parseFloat(ln.dataset.w) * pw;
    if (!natural || !pw || !want) continue;
    const r = Math.min(2, Math.max(0.5, want / natural));
    ln.style.transform = Math.abs(r - 1) < 0.01 ? '' : `scaleX(${r.toFixed(4)})`;
  }
}

// ── 모드 ────────────────────────────────────────────────
function setMode(mode, keepPlace = true) {
  const page = state.current;
  state.mode = mode;
  el.modeOriginal.setAttribute('aria-pressed', String(mode === 'original'));
  el.modeReading.setAttribute('aria-pressed', String(mode === 'reading'));
  el.pageList.classList.toggle('is-original', mode === 'original');
  el.pageList.classList.toggle('is-reading', mode === 'reading');
  el.modeHelp.textContent = MODE_HELP[mode];
  const done = [];
  state.pages.forEach((s, i) => { if (s.status === 'done') done.push(i + 1); });
  renderPages(done);
  if (keepPlace && state.doc) scrollToPage(page, 'instant');
}

// ── 4. 목차 ─────────────────────────────────────────────
function buildToc() {
  if (!state.doc) return;
  const items = state.doc.outline_source === 'bookmarks'
    ? state.doc.outline.map((o) => ({ title: o.title, page: o.page, level: o.level, block: '' }))
    : headingItems();
  const root = document.createDocumentFragment();
  const stack = [{ ol: null, level: 0, li: null }];
  const top = document.createElement('ol');
  stack[0].ol = top;
  for (const it of items) {
    // 깊이는 바로 앞 항목보다 한 단계까지만 깊어지게(책갈피 단계가 건너뛰어도 목록이 깨지지 않게)
    while (stack.length > 1 && stack[stack.length - 1].level >= it.level) stack.pop();
    let parent = stack[stack.length - 1];
    if (parent.li && it.level > parent.level) {
      const ol = document.createElement('ol');
      parent.li.append(ol);
      parent = { ol, level: parent.level, li: null };
    }
    const li = document.createElement('li');
    const a = make('a', '');
    a.href = it.block ? `#${it.block}` : `#p${it.page}`;
    a.dataset.page = String(it.page);
    if (it.block) a.dataset.block = it.block;
    a.append(make('span', 'toc__text', it.title), make('span', 'toc__page', `${it.page}쪽`));
    li.append(a);
    parent.ol.append(li);
    stack.push({ ol: parent.ol, level: it.level, li });
  }
  root.append(...top.childNodes);
  el.tocList.replaceChildren(root);
  const loading = state.doc.outline_source !== 'bookmarks' && state.pages.some((s) => s.status !== 'done');
  el.tocEmpty.hidden = items.length > 0;
  el.tocEmpty.textContent = loading ? '불러온 쪽에서 제목을 찾고 있어요…' : '목차가 없는 문서예요.';
  markTocCurrent();
}

function headingItems() {
  const out = [];
  state.pages.forEach((s, i) => {
    if (s.status !== 'done') return;
    for (const b of s.data.blocks) {
      if (b.type === 'text' && (b.role === 'h1' || b.role === 'h2')) {
        out.push({ title: b.text.trim().slice(0, 200), page: i + 1, level: b.role === 'h1' ? 1 : 2, block: b.id });
      }
    }
  });
  return out;
}

function markTocCurrent() {
  // 지금 쪽에 항목이 있으면 그 쪽의 첫 항목(장 제목), 없으면 앞쪽의 마지막 항목(지금 읽는 장)
  const links = [...el.tocList.querySelectorAll('a')];
  let best = links.find((a) => Number(a.dataset.page) === state.current) || null;
  if (!best) for (const a of links) if (Number(a.dataset.page) < state.current) best = a;
  if (!best && links.length) best = links[0];
  for (const a of links) {
    if (a === best) a.setAttribute('aria-current', 'true');
    else a.removeAttribute('aria-current');
  }
}

const isWide = () => window.matchMedia('(min-width: 1000px)').matches;

function setTocOpen(open) {
  state.tocOpen = open;
  el.tocToggle.setAttribute('aria-expanded', String(open));
  el.toc.hidden = !(open || isWide());
}

// ── 5. 쪽 이동 ──────────────────────────────────────────
function goTo(page, block = '', focus = true) {
  if (!state.doc) return;
  page = Math.min(state.doc.pages, Math.max(1, Math.round(page)));
  const slot = state.pages[page - 1];
  if (slot.status !== 'done') {
    // 아직 안 불러온 쪽: 그 쪽이 든 묶음을 다음 차례로 앞당기고, 불러오면 그리로 간다.
    if (slot.status === 'error') { retryBatch(page); }
    state.priority = page;
    state.target = { page, block };
    pump();
  } else {
    state.target = null;
  }
  const target = (block && document.getElementById(block)) || slot.section;
  scrollToEl(target, focus);
  state.current = page;
  syncPageUi();
}

function scrollToEl(target, focus) {
  const behavior = 'instant';
  target.scrollIntoView({ block: 'start', behavior });
  if (focus) {
    if (target.tabIndex < 0 && !target.hasAttribute('tabindex')) target.tabIndex = -1;
    target.focus({ preventScroll: true });
  }
}

function scrollToPage(page, behavior) {
  const slot = state.pages[page - 1];
  if (slot) slot.section.scrollIntoView({ block: 'start', behavior });
}

function goToTargetIfReady() {
  const t = state.target;
  if (!t || state.pages[t.page - 1].status !== 'done') return;
  state.target = null;
  const target = (t.block && document.getElementById(t.block)) || state.pages[t.page - 1].section;
  scrollToEl(target, document.activeElement === state.pages[t.page - 1].section);
}

// 화면 맨 위(도구 막대 바로 아래)에 있는 쪽을 지금 쪽으로 본다.
let currentFrame = 0;
function updateCurrent() {
  currentFrame = 0;
  if (!state.doc) return;
  const listRect = el.pageList.getBoundingClientRect();
  const barBottom = el.bar.hidden ? 0 : Math.max(0, el.bar.getBoundingClientRect().bottom);
  const x = Math.min(window.innerWidth - 2, Math.max(1, listRect.left + listRect.width / 2));
  let page = 0;
  for (const dy of [24, 80, 160, 260]) {
    const hit = document.elementFromPoint(x, Math.min(window.innerHeight - 2, barBottom + dy));
    const sec = hit && hit.closest && hit.closest('.pg');
    if (sec) { page = Number(sec.dataset.page); break; }
  }
  if (!page) {
    // 쪽 사이 빈틈: 위쪽이 화면 위를 지난 마지막 쪽
    for (const s of state.pages) {
      if (s.section.getBoundingClientRect().top <= barBottom + 24) page = Number(s.section.dataset.page);
      else break;
    }
    page = page || 1;
  }
  if (page !== state.current) {
    state.current = page;
    syncPageUi();
  }
}
function scheduleCurrent() {
  if (!currentFrame) currentFrame = requestAnimationFrame(updateCurrent);
}

function syncPageUi() {
  if (document.activeElement !== el.pageInput) el.pageInput.value = String(state.current);
  markTocCurrent();
}

// ── 진행 · 인쇄 ─────────────────────────────────────────
function updateProgress() {
  if (!state.doc) return;
  const n = state.doc.pages;
  const done = state.pages.filter((s) => s.status === 'done').length;
  const failed = state.pages.filter((s) => s.status === 'error').length;
  const all = done === n;
  el.progress.textContent = all ? `${num(n)}쪽 모두 불러옴`
    : `${num(done)}/${num(n)}쪽 불러옴${failed ? ` · ${num(failed)}쪽 실패` : ''}`;
  el.print.setAttribute('aria-disabled', String(!all));
  el.print.title = all ? '인쇄하거나 PDF로 저장' : '모두 불러온 뒤 인쇄할 수 있어요';
  el.printHint.hidden = all;
  el.printHint.textContent = failed && !state.loading ? '불러오지 못한 쪽이 있어요. [다시 시도]로 모두 불러온 뒤 인쇄할 수 있어요.'
    : '인쇄는 모두 불러온 뒤 할 수 있어요.';
  if (all && !state.announcedDone) {
    state.announcedDone = true;
    announce(`모두 불러왔어요, ${num(n)}쪽.`);
  }
}

function retryBatch(page) {
  const bs = batchStart(page);
  for (let p = bs; p <= Math.min(bs + BATCH - 1, state.pages.length); p += 1) {
    if (state.pages[p - 1].status === 'error') setPageStatus(p, 'pending');
  }
  state.priority = page;
  updateProgress();
  pump();
}

// ── 8. 내려받기 (SPEC §10.7과 같은 요청·이름·문구) ───────
function setDownloadError(parts) {
  if (!parts) { el.downloadError.hidden = true; el.downloadError.replaceChildren(); return; }
  fillMessage(el.downloadError, parts);
  el.downloadError.hidden = false;
}

async function download() {
  if (state.download || !state.source) return;
  const name = exportFileName(state.source.fileName);
  const d = { controller: new AbortController(), startedAt: Date.now(), timedOut: false, ticker: 0 };
  const timer = setTimeout(() => { d.timedOut = true; d.controller.abort(); }, TIMEOUTS.export);
  state.download = d;
  el.download.disabled = true;
  el.download.textContent = '다운로드 파일 만드는 중…';
  setDownloadError(null);
  el.downloadStatus.classList.add('is-busy');
  const tick = () => { el.downloadStatus.textContent = `다운로드 파일을 만들고 있어요… ${Math.floor((Date.now() - d.startedAt) / 1000)}초`; };
  tick();
  d.ticker = setInterval(tick, 1000);
  let code = null;
  let blob = null;
  let omitted = 0;
  try {
    const res = await fetch('/api/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/pdf', 'X-Filename': encodeURIComponent(state.source.fileName) },
      body: state.source.blob, signal: d.controller.signal,
    });
    if (!res.ok) {
      try {
        const body = await res.json();
        if (body && body.error && typeof body.error.code === 'string') code = body.error.code;
      } catch { /* JSON 아님 */ }
      code = code || (res.status === 409 ? 'BUSY' : res.status === 504 ? 'EXTRACT_TIMEOUT' : 'UNKNOWN');
    } else {
      blob = await res.blob();
      omitted = parseInt(res.headers.get('X-Figures-Omitted') || '0', 10) || 0;
    }
  } catch {
    code = d.timedOut ? 'CLIENT_TIMEOUT' : 'NETWORK';
  }
  clearTimeout(timer);
  clearInterval(d.ticker);
  state.download = null;
  const refocus = document.activeElement === document.body || document.activeElement === el.download;
  el.download.disabled = false;
  el.download.textContent = '추출한 텍스트 다운로드하기';
  el.downloadStatus.classList.remove('is-busy');
  if (refocus) el.download.focus();
  if (code) {
    el.downloadStatus.textContent = '';
    setDownloadError(msgOf(code));
    return;
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.hidden = true;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
  el.downloadStatus.textContent = `${name}을(를) 내려받았어요.` + (omitted > 0 ? ` 그림 ${num(omitted)}개는 크기·시간 제한으로 빠졌어요.` : '');
}

// ── 이벤트 ──────────────────────────────────────────────
function onClick(e) {
  const link = e.target.closest('#toc-list a');
  if (link) {
    // 주소의 #doc=TOKEN을 지키려고 해시 이동 대신 직접 스크롤한다(새로고침해도 다시 열리게).
    e.preventDefault();
    if (!isWide()) setTocOpen(false);
    goTo(Number(link.dataset.page), link.dataset.block || '');
    return;
  }
  const t = e.target.closest('[data-action]');
  if (!t || t.disabled) return;
  switch (t.dataset.action) {
    case 'mode': if (state.doc && t.dataset.mode !== state.mode) setMode(t.dataset.mode); break;
    case 'toc':
      setTocOpen(!state.tocOpen);
      // 열면 첫 항목으로 포커스(Esc로 닫으면 [목차]로 돌아온다). 항목이 없으면 단추에 그대로.
      if (state.tocOpen) el.tocList.querySelector('a')?.focus();
      break;
    case 'prev': goTo(state.current - 1, '', false); break;
    case 'next': goTo(state.current + 1, '', false); break;
    case 'print':
      if (t.getAttribute('aria-disabled') === 'true') { announce(el.printHint.textContent); break; }
      window.print();
      break;
    case 'download': download(); break;
    case 'retry-batch': retryBatch(Number(t.dataset.page)); break;
    case 'reconnect': connect(); break;
    case 'restart': if (state.source) { state.run += 1; startLoading(); } else connect(); break;
  }
}

function measureBar() {
  const sticky = getComputedStyle(el.bar).position === 'sticky' && !el.bar.hidden;
  document.documentElement.style.setProperty('--bar-h', `${sticky ? Math.ceil(el.bar.getBoundingClientRect().height) : 0}px`);
}

let lastWidth = 0;
function onResize() {
  measureBar();
  if (!state.tocOpen) el.toc.hidden = !isWide() || !state.doc;
  else el.toc.hidden = !state.doc;
  // 원본 배치의 줄 폭 비율은 화면 폭에 거의 무관하지만, 폭이 크게 바뀌면(글자 크기 반올림) 다시 맞춘다.
  const w = el.pageList.clientWidth;
  if (state.mode === 'original' && lastWidth && Math.abs(w - lastWidth) / lastWidth > 0.2) {
    fitLines([...el.pageList.querySelectorAll('.paper .ln')]);
  }
  lastWidth = w;
  scheduleCurrent();
}

function init() {
  if (location.protocol === 'file:') {
    el.boot.classList.add('is-shown');
    return;
  }
  document.documentElement.classList.add('app-ready');
  const m = /(?:^#|&)doc=([^&]*)/.exec(location.hash);
  try { state.token = m ? decodeURIComponent(m[1]) : ''; } catch { state.token = ''; }
  document.addEventListener('click', onClick);
  el.pageInput.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const v = parseInt(el.pageInput.value, 10);
    if (Number.isFinite(v)) goTo(v);
  });
  el.pageInput.addEventListener('blur', () => { el.pageInput.value = String(state.current); });
  window.addEventListener('scroll', scheduleCurrent, { passive: true });
  window.addEventListener('resize', onResize);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && state.tocOpen && !isWide()) { setTocOpen(false); el.tocToggle.focus(); }
  });
  if (typeof ResizeObserver === 'function') new ResizeObserver(measureBar).observe(el.bar);
  lastWidth = el.pageList.clientWidth;
  connect();
}

init();
