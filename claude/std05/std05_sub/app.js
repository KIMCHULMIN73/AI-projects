// 한눈 PDF 요약 — 클라이언트 (ES 모듈 하나, 빌드 없음)
//
// 계약(SPEC.md §4):
//   POST /api/extract   원본 PDF 바이트 + X-Filename → 200 {text, meta}
//   POST /api/summarize {text, meta}                → 200 {one_line, overview, points, model, calls, chunks, elapsed_ms}
//   POST /api/export    원본 PDF 바이트 + X-Filename → 200 text/html 한 파일(내려받기, SPEC §10.3·§10.6)
//   실패는 모두 {"error": {"code", "message", "reset_at"?}}
//   보기 창(viewer.html)은 BroadcastChannel 'pdf-summary-viewer'로 이 창에서 File을 넘겨받는다(SPEC §10.0·§10.7).
// 규칙: 서버·모델·파일 이름 문자열은 모두 textContent로만 넣는다(innerHTML을 쓰지 않는다).
//       오류 문구는 서버 message를 쓰지 않고 code로 여기서 정한다(SPEC §7.4).
//       추출 결과는 메모리에만 둔다(localStorage 등 저장 없음, SPEC §7.1).

// server.py의 MAX_FILE_BYTES와 같은 값(SPEC §3). 서버도 다시 검사한다.
const MAX_FILE_BYTES = 20 * 1024 * 1024;
// 이보다 긴 문서는 오래 걸린다고 미리 알린다(SPEC §7.1의 2만 자). llm.py는 6만 자까지 나누지 않고 한 번에 보낸다 —
// 2026-11-17 실측: 5.6만 자 15.9초, 5.8만 자 22.3초(폴백 포함). 짧은 문서는 5~10초.
const LONG_DOC_CHARS = 20_000;

// 화면 요청 제한시간(SPEC §3). 시험 스크립트만 globalThis.__pdfSummaryTimeouts로 줄인다.
const TIMEOUTS = Object.assign({ extract: 60_000, summarize: 180_000, health: 8_000, export: 200_000 },
  globalThis.__pdfSummaryTimeouts || {});

// ── 오류 문구 (SPEC §7.4) ───────────────────────────────
// 문자열과 {code: '...'}(고정 명령어를 <code>로 보여줄 부분)의 배열. 모두 이 파일에 쓴 고정 문구다.
const MSG = {
  NOT_PDF: ['PDF 파일만 요약할 수 있어요. 확장자가 .pdf인 파일을 골라 주세요.'],
  EMPTY_FILE: ['빈 파일이에요. 내용이 있는 PDF를 골라 주세요.'],
  MULTIPLE_FILES: ['한 번에 파일 하나만 요약할 수 있어요.'],
  FILE_TOO_LARGE: ['파일이 너무 커요(20MB까지). 쪽을 나눠 저장해 올려 주세요.'],
  TOO_LONG: ['문서가 너무 길어요(300쪽까지). 필요한 부분만 따로 저장해 올려 주세요.'],
  ENCRYPTED: ['암호가 걸린 PDF예요. 암호를 푼 뒤 다시 저장해서 올려 주세요.'],
  BROKEN_PDF: ['PDF를 열 수 없어요. 파일이 손상됐을 수 있어요. 다시 내려받거나 다시 저장해 보세요.'],
  NO_TEXT: ['이 PDF에서 글자를 찾지 못했어요. 스캔한 이미지로 된 문서는 아직 요약할 수 없어요(글자 인식 미지원).'],
  EXTRACT_TIMEOUT: ['PDF를 읽는 데 너무 오래 걸려 멈췄어요. 더 작은 파일로 시도해 주세요.'],
  TOOL_MISSING: ['PDF를 읽는 프로그램이 설치되어 있지 않아요. 관리자에게 ', { code: 'sudo apt install poppler-utils' }, ' 설치를 요청하세요.'],
  NO_KEY: ['AI 연결 설정(API 키)이 없어요. 터미널에서 ', { code: 'python3 -B config.py' }, '로 확인해 주세요.'],
  BUSY: ['다른 요약이 진행 중이에요. 끝난 뒤 다시 눌러 주세요.'],
  RATE_LIMIT: ['요청이 잠깐 몰렸어요. 1분쯤 뒤에 다시 시도해 주세요.'],
  // DAILY_LIMIT은 시각이 들어가므로 dailyLimitMessage()가 만든다.
  TIMEOUT: ['요약이 너무 오래 걸려 멈췄어요. 잠시 뒤 다시 시도해 주세요.'],
  CLIENT_TIMEOUT: ['요약이 너무 오래 걸려 멈췄어요. 잠시 뒤 다시 시도해 주세요.'],
  // 추출 단계의 CLIENT_TIMEOUT: §7.4 문구는 '요약'을 말하므로 읽기 단계에 맞게 바꿨다(다시 시도는 같은 파일로 추출).
  CLIENT_TIMEOUT_EXTRACT: ['PDF를 읽는 데 너무 오래 걸려 멈췄어요. 잠시 뒤 다시 시도하거나 더 작은 파일로 시도해 주세요.'],
  UPSTREAM: ['AI가 요약을 제대로 돌려주지 못했어요. 다시 시도해 주세요.'],
  BAD_OUTPUT: ['AI가 요약을 제대로 돌려주지 못했어요. 다시 시도해 주세요.'],
  NETWORK: ['서버에 연결할 수 없어요. ', { code: './run.sh' }, '가 실행 중인지 확인해 주세요.'],
  UNKNOWN: ['알 수 없는 문제가 생겼어요. 다른 파일로 시도하거나 새로고침해 주세요.'],
};
const KNOWN_CODES = new Set([...Object.keys(MSG), 'DAILY_LIMIT']);

// 요약 단계에서 [다시 시도]를 보여줄 코드. DAILY_LIMIT은 다시 시도 없음.
const RETRY_SUMMARIZE = new Set(['NO_KEY', 'BUSY', 'RATE_LIMIT', 'TIMEOUT', 'CLIENT_TIMEOUT', 'UPSTREAM', 'BAD_OUTPUT', 'NETWORK']);
// 추출 단계에서 [다시 시도](같은 파일로 다시 추출)를 보여줄 코드(SPEC §7.4 마지막 줄).
const RETRY_EXTRACT = new Set(['NETWORK', 'CLIENT_TIMEOUT']);

// ── 추출한 텍스트 보기·내려받기 오류 문구 (SPEC §10.7 표 — viewer.js에도 같은 표가 있다) ──
// 요약 흐름의 오류 화면과 섞이지 않게 문서 카드의 #doc-tools-error에만 보인다.
const TOOL_MSG = {
  TOOL_MISSING: ['표·그림을 읽는 프로그램이 없어요. 관리자에게 ', { code: 'sudo apt install poppler-utils' }, ' 설치를 요청하세요.'],
  BUSY: ['다른 창에서 문서를 읽고 있어요. 잠시 뒤 다시 눌러 주세요.'],
  EXTRACT_TIMEOUT: ['문서가 복잡해 시간이 너무 걸려 멈췄어요. 잠시 뒤 다시 시도해 주세요.'],
  CLIENT_TIMEOUT: ['문서가 복잡해 시간이 너무 걸려 멈췄어요. 잠시 뒤 다시 시도해 주세요.'],
  ENCRYPTED: MSG.ENCRYPTED, BROKEN_PDF: MSG.BROKEN_PDF, NOT_PDF: MSG.NOT_PDF, EMPTY_FILE: MSG.EMPTY_FILE,
  TOO_LONG: MSG.TOO_LONG, FILE_TOO_LARGE: MSG.FILE_TOO_LARGE,
  NETWORK: MSG.NETWORK,
  UNKNOWN: ['알 수 없는 문제가 생겼어요. 다시 시도해 주세요.'],
};
const VIEWER_CHANNEL = 'pdf-summary-viewer';

// 코드가 없거나 모르는 코드일 때 HTTP 상태로 짐작한다.
function codeFromStatus(status, stage) {
  if (status === 429) return 'RATE_LIMIT';
  if (status === 409) return 'BUSY';
  if (status === 504) return stage === 'extract' ? 'EXTRACT_TIMEOUT' : 'TIMEOUT';
  if ((status === 502 || status === 503) && stage === 'summarize') return 'UPSTREAM';
  return 'UNKNOWN';
}

const PHRASES = {
  short: [
    '문서를 처음부터 끝까지 읽고 있어요.',
    '중요한 문장에 밑줄을 긋고 있어요.',
    '핵심 요점을 고르고 있어요.',
    '한 줄로 줄여 보는 중이에요.',
    '읽기 쉬운 한국어로 다듬고 있어요.',
  ],
  long: [
    '긴 문서라 처음부터 끝까지 읽는 데 시간이 걸려요.',
    '부분마다 중요한 내용을 추리고 있어요.',
    '추린 내용을 하나로 모으고 있어요.',
    '전체 흐름이 보이게 정리하는 중이에요.',
    '읽기 쉬운 한국어로 다듬고 있어요.',
  ],
};
const PHRASE_EVERY_S = 6;
const SLOW_AFTER_S = 60;

// ── DOM ─────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const el = {
  main: $('main'), boot: $('boot-notice'), announcer: $('announcer'), health: $('health-notices'),
  upload: $('upload'), drop: $('drop-zone'), pick: $('pick-button'), input: $('file-input'),
  doc: $('doc'), docTitle: $('doc-title'), docPages: $('doc-pages'), docChars: $('doc-chars'), docSize: $('doc-size'),
  docFile: $('doc-file'), docTruncated: $('doc-truncated'), docEmpty: $('doc-empty-pages'), docStopped: $('doc-stopped'),
  docActions: $('doc-actions'), summarize: $('summarize-button'),
  progress: $('progress'), progressTitle: $('progress-title'), progressFile: $('progress-file'),
  progressPhrase: $('progress-phrase'), progressHint: $('progress-hint'), cancel: $('cancel-button'),
  error: $('error'), errorTitle: $('error-title'), errorText: $('error-text'), retry: $('retry-button'), another: $('another-button'),
  result: $('result'), resultHeading: $('result-one-title'), resultOne: $('result-one'), resultOverview: $('result-overview'),
  resultPoints: $('result-points'), resultTruncated: $('result-truncated'), resultMeta: $('result-meta'),
  copyStatus: $('copy-status'),
  viewText: $('view-text-button'), downloadText: $('download-text-button'),
  toolsStatus: $('doc-tools-status'), toolsError: $('doc-tools-error'),
};

// ── 상태 ────────────────────────────────────────────────
// phase: ready · extracting · extracted · summarizing · result · error
const state = {
  phase: 'ready',
  file: null,          // 마지막으로 올린 File (추출 단계 다시 시도용)
  doc: null,           // { text, meta } — /api/extract 결과. 메모리에만
  summary: null,       // /api/summarize 결과
  error: null,         // { code, stage: 'pre'|'extract'|'summarize', resetAt }
  stopped: false,      // 사용자가 요약을 취소했음(문서 카드에 한 줄 안내)
  seq: 0,              // 요청마다 1씩 — 늦게 온 이전 응답을 버리는 데 쓴다
  pending: null,       // { seq, controller, startedAt, ticker, timedOut }
  // 아래는 요약 흐름(phase)과 따로 움직인다 — 보기·내려받기는 요약 중에도 쓸 수 있다(SPEC §10.0).
  docToken: null,      // 추출에 성공할 때마다 새로 — 보기 창이 이 값으로 문서를 달라고 한다
  layoutTools: true,   // /api/health의 layout_tools에 false가 있으면 false → 두 버튼 비활성
  download: null,      // 진행 중인 내려받기 { token, controller, ticker, timer, timedOut, startedAt }
};

// ── 그리기 ──────────────────────────────────────────────
// 상태 → 어떤 구역이 보일지만 정한다. 구역 안 내용은 바뀔 때(성공·오류·진행) 한 번씩 채운다.
function render() {
  const { phase, error } = state;
  const busy = phase === 'extracting' || phase === 'summarizing';
  const errStage = phase === 'error' ? error.stage : null;

  el.upload.hidden = !(phase === 'ready' || phase === 'extracting' || errStage === 'pre' || errStage === 'extract');
  setDropEnabled(phase !== 'extracting');

  el.doc.hidden = !(state.doc && (phase === 'extracted' || phase === 'summarizing' || phase === 'result' || errStage === 'summarize'));
  el.docActions.hidden = !(phase === 'extracted' || phase === 'summarizing');
  el.summarize.disabled = phase !== 'extracted';
  el.summarize.textContent = phase === 'summarizing' ? '요약하는 중…' : '요약하기';
  el.docStopped.hidden = !(phase === 'extracted' && state.stopped);

  const downloading = !!state.download;
  el.viewText.disabled = !state.layoutTools;
  el.downloadText.disabled = !state.layoutTools || downloading;
  el.downloadText.textContent = downloading ? '다운로드 파일 만드는 중…' : '추출한 텍스트 다운로드하기';

  el.progress.hidden = !busy;
  el.error.hidden = phase !== 'error';
  el.result.hidden = phase !== 'result';
}

function setDropEnabled(on) {
  el.drop.classList.toggle('is-disabled', !on);
  el.pick.disabled = !on;
  if (!on) el.drop.classList.remove('is-over');
}

function announce(text) {
  // 같은 문장을 연달아 넣어도 다시 읽히도록 비웠다가 넣는다.
  el.announcer.textContent = '';
  setTimeout(() => { el.announcer.textContent = text; }, 50);
}

function fillMessage(target, parts) {
  target.replaceChildren();
  const p = document.createElement('p');
  for (const part of parts) {
    if (typeof part === 'string') p.append(part);
    else { const c = document.createElement('code'); c.textContent = part.code; p.append(c); }
  }
  target.append(p);
}

const nf = new Intl.NumberFormat('ko-KR');
const num = (n) => nf.format(n);

function formatBytes(n) {
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1).replace(/\.0$/, '')}MB`;
  if (n >= 1024) return `${Math.round(n / 1024)}KB`;
  return `${n}바이트`;
}

function docTitle(meta) {
  return meta.title || meta.file_name || '제목 없는 문서';
}

function fillDoc() {
  const m = state.doc.meta;
  el.docTitle.textContent = docTitle(m);
  el.docPages.textContent = `${num(m.pages)}쪽`;
  el.docChars.textContent = m.truncated ? `${num(m.chars_total)}자 중 ${num(m.chars)}자` : `${num(m.chars)}자`;
  el.docSize.textContent = m.file_bytes ? formatBytes(m.file_bytes) : '—';
  const showFile = m.title && m.file_name && m.title !== m.file_name;
  el.docFile.hidden = !showFile;
  el.docFile.textContent = showFile ? `파일 이름: ${m.file_name}` : '';
  el.docTruncated.hidden = !m.truncated;
  el.docTruncated.textContent = m.truncated
    ? `문서가 길어 앞 ${num(m.pages_used)}쪽(약 ${num(m.chars)}자)까지만 요약합니다.` : '';
  el.docEmpty.hidden = !(m.empty_pages > 0);
  el.docEmpty.textContent = m.empty_pages > 0
    ? `글자가 없는 쪽 ${num(m.empty_pages)}개는 빼고 읽었어요(스캔된 쪽일 수 있어요).` : '';
}

function fillResult() {
  const s = state.summary;
  const m = state.doc.meta;
  el.resultOne.textContent = s.one_line;
  el.resultOverview.textContent = s.overview;
  el.resultPoints.replaceChildren(...s.points.map((p) => {
    const li = document.createElement('li');
    li.textContent = p;
    return li;
  }));
  el.resultTruncated.hidden = !m.truncated;
  el.resultTruncated.textContent = m.truncated ? `문서가 길어 앞 ${num(m.pages_used)}쪽까지만 요약했어요.` : '';
  const bits = [];
  if (s.model) bits.push(`모델 ${s.model}`);
  if (Number.isFinite(s.calls)) bits.push(`호출 ${num(s.calls)}회`);
  if (Number.isFinite(s.elapsed_ms)) bits.push(`${(s.elapsed_ms / 1000).toFixed(1)}초`);
  el.resultMeta.textContent = bits.join(' · ');
  el.resultMeta.hidden = bits.length === 0;
  el.copyStatus.textContent = '';
}

function dailyLimitMessage(resetAt) {
  return [`오늘 쓸 수 있는 무료 요약 횟수를 다 썼어요. ${resetPhrase(resetAt)}부터 다시 요약할 수 있어요.`];
}

// 한도가 풀리는 시각을 "내일 오전 9시"처럼. reset_at이 없거나 이미 지났으면 다음 09:00 KST(= 00:00 UTC)로 계산한다.
function resetPhrase(resetAt, now = new Date()) {
  let t = Number.isFinite(resetAt) && resetAt > now.getTime() ? new Date(resetAt) : null;
  if (!t) {
    t = new Date(now);
    t.setUTCHours(24, 0, 0, 0);
  }
  const startOf = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOf(t) - startOf(now)) / 86_400_000);
  const day = days === 0 ? '오늘' : days === 1 ? '내일' : days === 2 ? '모레' : `${t.getMonth() + 1}월 ${t.getDate()}일`;
  const h = t.getHours();
  const ampm = h < 12 ? '오전' : '오후';
  const h12 = h % 12 === 0 ? 12 : h % 12;
  const min = t.getMinutes() ? ` ${t.getMinutes()}분` : '';
  return `${day} ${ampm} ${h12}시${min}`;
}

function showError(code, stage, resetAt = null) {
  if (!KNOWN_CODES.has(code)) code = 'UNKNOWN';
  state.error = { code, stage, resetAt };
  state.phase = 'error';
  stopPending();
  render();

  el.errorTitle.textContent = stage === 'summarize' ? '요약하지 못했어요'
    : stage === 'extract' ? 'PDF를 읽지 못했어요' : '이 파일은 올릴 수 없어요';
  let parts;
  if (code === 'DAILY_LIMIT') parts = dailyLimitMessage(resetAt);
  else if (code === 'CLIENT_TIMEOUT' && stage === 'extract') parts = MSG.CLIENT_TIMEOUT_EXTRACT;
  else parts = MSG[code];
  fillMessage(el.errorText, parts);

  const canRetry = stage === 'summarize' ? RETRY_SUMMARIZE.has(code)
    : stage === 'extract' ? RETRY_EXTRACT.has(code) && !!state.file : false;
  el.retry.hidden = !canRetry;
  (canRetry ? el.retry : el.another).focus();
}

// ── 진행 표시 ───────────────────────────────────────────
function startProgress(kind) {
  const p = state.pending;
  const long = kind === 'summarize' && state.doc.meta.chars > LONG_DOC_CHARS;
  const phrases = long ? PHRASES.long : PHRASES.short;
  if (kind === 'extract') {
    el.progressTitle.textContent = 'PDF에서 글자를 읽고 있어요…';
    el.progressFile.textContent = `${state.file.name || '이름 없는 파일'} · ${formatBytes(state.file.size)}`;
    el.progressPhrase.textContent = '';
    el.progressHint.hidden = true;
    el.cancel.textContent = '읽기 취소';
  } else {
    el.progressTitle.textContent = '요약하고 있어요… 0초';
    el.progressFile.textContent = docTitle(state.doc.meta);
    el.progressPhrase.textContent = phrases[0];
    el.progressHint.hidden = !long;
    el.progressHint.textContent = long ? '긴 문서라 30초~1분쯤 걸릴 수 있어요.' : '';
    el.cancel.textContent = '요약 취소';
  }
  const tick = () => {
    const s = Math.floor((Date.now() - p.startedAt) / 1000);
    if (kind === 'extract') {
      if (s >= 3) el.progressPhrase.textContent = `${s}초째 읽는 중이에요. 쪽이 많으면 조금 더 걸려요.`;
      return;
    }
    el.progressTitle.textContent = `요약하고 있어요… ${s}초`;
    el.progressPhrase.textContent = s >= SLOW_AFTER_S
      ? '생각보다 오래 걸리고 있어요. 3분이 지나도 끝나지 않으면 멈추고 알려 드릴게요.'
      : phrases[Math.floor(s / PHRASE_EVERY_S) % phrases.length];
  };
  p.ticker = setInterval(tick, 1000);
}

function stopPending() {
  const p = state.pending;
  if (!p) return;
  clearInterval(p.ticker);
  clearTimeout(p.timer);
  state.pending = null;
}

// ── 서버 호출 ───────────────────────────────────────────
class ApiError extends Error {
  constructor(code, resetAt = null) { super(code); this.code = code; this.resetAt = resetAt; }
}

// 요청 하나. 성공이면 해석한 JSON, 실패면 ApiError(code). 취소면 ApiError('ABORTED').
async function request(url, init, stage, p) {
  let res;
  try {
    res = await fetch(url, { ...init, signal: p.controller.signal });
  } catch {
    if (p.timedOut) throw new ApiError('CLIENT_TIMEOUT');
    if (p.controller.signal.aborted) throw new ApiError('ABORTED');
    throw new ApiError('NETWORK');
  }
  let body = null;
  try {
    body = await res.json();
  } catch {
    if (p.timedOut) throw new ApiError('CLIENT_TIMEOUT');
    if (p.controller.signal.aborted) throw new ApiError('ABORTED');
  }
  if (!res.ok) {
    const err = body && typeof body === 'object' && body.error && typeof body.error === 'object' ? body.error : {};
    const code = typeof err.code === 'string' && KNOWN_CODES.has(err.code) ? err.code : codeFromStatus(res.status, stage);
    const resetAt = Number.isFinite(err.reset_at) ? err.reset_at : null;
    throw new ApiError(code, resetAt);
  }
  if (!body || typeof body !== 'object') throw new ApiError(stage === 'summarize' ? 'BAD_OUTPUT' : 'UNKNOWN');
  return body;
}

function begin(kind) {
  const p = { seq: ++state.seq, controller: new AbortController(), startedAt: Date.now(), ticker: 0, timer: 0, timedOut: false };
  p.timer = setTimeout(() => { p.timedOut = true; p.controller.abort(); }, TIMEOUTS[kind]);
  state.pending = p;
  return p;
}

// 응답이 왔을 때 아직 이 요청을 기다리고 있는지. 취소·새 파일로 넘어갔으면 false.
const isCurrent = (p) => state.pending === p && state.seq === p.seq;

// /api/extract 응답을 계약 모양으로 정리. 모양이 틀리면 null.
function normalizeExtract(body) {
  if (typeof body.text !== 'string' || !body.text.trim() || !body.meta || typeof body.meta !== 'object') return null;
  const m = body.meta;
  const int = (v) => (Number.isFinite(v) && v >= 0 ? Math.floor(v) : 0);
  const str = (v) => (typeof v === 'string' ? v : '');
  const meta = {
    file_name: str(m.file_name), file_bytes: int(m.file_bytes), title: str(m.title).trim(),
    pages: int(m.pages), pages_used: int(m.pages_used), empty_pages: int(m.empty_pages),
    chars: int(m.chars) || body.text.length, chars_total: int(m.chars_total), truncated: m.truncated === true,
  };
  if (!meta.pages_used) meta.pages_used = meta.pages;
  if (!meta.chars_total) meta.chars_total = meta.chars;
  return { text: body.text, meta };
}

function normalizeSummary(body) {
  const str = (v) => (typeof v === 'string' ? v.trim() : '');
  const points = Array.isArray(body.points) ? body.points.map(str).filter(Boolean) : [];
  const out = {
    one_line: str(body.one_line), overview: str(body.overview), points,
    model: str(body.model),
    calls: Number.isFinite(body.calls) ? body.calls : NaN,
    elapsed_ms: Number.isFinite(body.elapsed_ms) ? body.elapsed_ms : NaN,
  };
  return out.one_line && out.overview && out.points.length ? out : null;
}

// ── 흐름 ────────────────────────────────────────────────
function precheck(files) {
  if (files.length > 1) return 'MULTIPLE_FILES';
  const f = files[0];
  if (!/\.pdf$/i.test(f.name) && f.type !== 'application/pdf') return 'NOT_PDF';
  if (f.size === 0) return 'EMPTY_FILE';
  if (f.size > MAX_FILE_BYTES) return 'FILE_TOO_LARGE';
  return null;
}

// folder: 놓은 것 중 폴더가 있었는지. 폴더는 NOT_PDF로 본다(SPEC §7.1).
function acceptFiles(files, folder = false) {
  if (state.phase === 'extracting' || state.phase === 'summarizing') return;   // 처리 중에는 무시(SPEC §7.1)
  if (!files.length) return;
  const code = files.length > 1 ? 'MULTIPLE_FILES' : folder ? 'NOT_PDF' : precheck(files);
  if (code) {
    state.file = null;
    state.docToken = null;
    resetDocTools();
    showError(code, 'pre');
    return;
  }
  state.file = files[0];
  extract();
}

async function extract() {
  const file = state.file;
  state.doc = null;
  state.summary = null;
  state.stopped = false;
  state.docToken = null;
  resetDocTools();
  const p = begin('extract');
  state.phase = 'extracting';
  render();
  startProgress('extract');
  el.cancel.focus();
  announce(`${file.name || '파일'}에서 글자를 읽고 있어요.`);
  try {
    const body = await request('/api/extract', {
      method: 'POST',
      headers: { 'Content-Type': 'application/pdf', 'X-Filename': encodeURIComponent(file.name || '') },
      body: file,
    }, 'extract', p);
    if (!isCurrent(p)) return;
    const doc = normalizeExtract(body);
    if (!doc) throw new ApiError('UNKNOWN');
    if (!doc.meta.file_name) doc.meta.file_name = file.name || '';
    if (!doc.meta.file_bytes) doc.meta.file_bytes = file.size;
    state.doc = doc;
    state.docToken = newDocToken();
    stopPending();
    state.phase = 'extracted';
    render();
    fillDoc();
    el.summarize.focus();
    const m = doc.meta;
    announce(`문서를 읽었어요. ${docTitle(m)}, ${num(m.pages)}쪽, ${num(m.chars)}자.`
      + (m.truncated ? ` 문서가 길어 앞 ${num(m.pages_used)}쪽까지만 요약합니다.` : '')
      + ' 요약하기 버튼을 누르면 요약을 시작해요.');
  } catch (e) {
    if (!isCurrent(p)) return;
    if (e.code === 'ABORTED') return;
    showError(e instanceof ApiError ? e.code : 'UNKNOWN', 'extract', e.resetAt);
  }
}

async function summarize() {
  if (state.phase !== 'extracted' && !(state.phase === 'error' && state.error.stage === 'summarize')) return;
  if (!state.doc) return;
  state.stopped = false;
  const p = begin('summarize');
  state.phase = 'summarizing';
  render();
  startProgress('summarize');
  el.cancel.focus();
  announce('요약하고 있어요. 끝나면 알려 드릴게요.');
  try {
    const body = await request('/api/summarize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: state.doc.text, meta: state.doc.meta }),
    }, 'summarize', p);
    if (!isCurrent(p)) return;
    const s = normalizeSummary(body);
    if (!s) throw new ApiError('BAD_OUTPUT');
    state.summary = s;
    stopPending();
    state.phase = 'result';
    render();
    fillResult();
    el.resultHeading.focus();
    announce(`요약이 끝났어요. 한 줄 요약: ${s.one_line}`);
  } catch (e) {
    if (!isCurrent(p)) return;
    if (e.code === 'ABORTED') return;
    showError(e instanceof ApiError ? e.code : 'UNKNOWN', 'summarize', e.resetAt);
  }
}

function cancel() {
  const p = state.pending;
  if (!p) return;
  const wasSummarizing = state.phase === 'summarizing';
  stopPending();          // 먼저 기다림을 끊어 isCurrent(p)가 false가 되게 한다
  p.controller.abort();
  if (wasSummarizing) {
    state.phase = 'extracted';
    state.stopped = true;
    render();
    el.summarize.focus();
    announce('요약을 멈췄어요.');
  } else {
    state.phase = 'ready';
    state.file = null;
    state.docToken = null;
    render();
    el.pick.focus();
    announce('읽기를 취소했어요. 다른 파일을 골라 주세요.');
  }
}

function retry() {
  if (state.phase !== 'error') return;
  if (state.error.stage === 'summarize') summarize();
  else if (state.error.stage === 'extract' && state.file) extract();
}

// 처음 화면으로 돌아가 바로 파일 고르기 창을 연다(버튼 클릭이 사용자 동작이라 열 수 있다).
function another() {
  if (state.pending) cancel();
  state.phase = 'ready';
  state.file = null;
  state.doc = null;
  state.summary = null;
  state.error = null;
  state.stopped = false;
  state.docToken = null;
  resetDocTools();
  render();
  el.pick.focus();
  openPicker();
}

function openPicker() {
  if (el.pick.disabled) return;
  el.input.value = '';          // 같은 파일을 다시 골라도 change가 나도록
  el.input.click();
}

// ── 복사 (SPEC §7.2) ────────────────────────────────────
function copyText() {
  const s = state.summary;
  const m = state.doc.meta;
  const lines = [docTitle(m), `한 줄 요약: ${s.one_line}`, '', '요약', s.overview, '', '핵심 요점', ...s.points.map((p) => `- ${p}`)];
  if (m.truncated) lines.push('', `※ 앞 ${m.pages_used}쪽까지만 요약했습니다.`);
  return lines.join('\n');
}

let copyTimer = 0;
async function copy() {
  if (!state.summary) return;
  const text = copyText();
  let ok = false;
  try {
    await navigator.clipboard.writeText(text);
    ok = true;
  } catch {
    ok = legacyCopy(text);
  }
  clearTimeout(copyTimer);
  el.copyStatus.textContent = ok ? '복사했어요' : '복사하지 못했어요. 직접 선택해 복사해 주세요.';
  el.copyStatus.classList.toggle('is-error', !ok);
  if (ok) copyTimer = setTimeout(() => { el.copyStatus.textContent = ''; }, 2000);
}

function legacyCopy(text) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.className = 'copy-buffer';
  document.body.append(ta);
  const back = document.activeElement;
  ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch { ok = false; }
  ta.remove();
  if (back && back.focus) back.focus();
  return ok;
}

// ── 추출한 텍스트 보기·내려받기 (SPEC §10.7) ────────────
// 요약 흐름(state.phase)을 건드리지 않는다. 결과·오류는 문서 카드의 #doc-tools-status·#doc-tools-error에만.

// 내려받을 파일 이름(SPEC §10.6 — 서버 pdf_layout.export_file_name과 같은 규칙):
// 끝의 .pdf 빼기 → 금지 문자·제어문자 _ → 앞뒤 공백·마침표 제거 → 100자(코드포인트) → {이름}_추출.html
function exportFileName(name) {
  let s = String(name || '').replace(/\.pdf$/i, '');
  s = s.replace(/[\\/:*?"<>|\u0000-\u001f\u007f-\u009f]/g, '_');
  s = s.replace(/^[\s.]+|[\s.]+$/g, '');
  s = Array.from(s).slice(0, 100).join('');
  return s ? `${s}_추출.html` : '추출한_텍스트.html';
}

function newDocToken() {
  return `${Date.now().toString(36)}-${state.seq}`;
}

function setToolsStatus(text) {
  el.toolsStatus.textContent = text;
}

// parts: fillMessage 형식. link: 팝업이 막혔을 때 보일 링크 주소(이 파일이 만든 값).
function setToolsError(parts, link = null) {
  if (!parts) {
    el.toolsError.hidden = true;
    el.toolsError.replaceChildren();
    return;
  }
  fillMessage(el.toolsError, parts);
  if (link) {
    const p = document.createElement('p');
    const a = document.createElement('a');
    a.href = link;
    a.target = '_blank';
    a.rel = 'noopener';
    a.textContent = '새 창에서 열기';
    p.append(a);
    el.toolsError.append(p);
  }
  el.toolsError.hidden = false;
}

// 문서가 바뀌거나 사라질 때: 진행 중인 내려받기를 멈추고 안내를 지운다.
function resetDocTools() {
  stopDownload(true);
  setToolsStatus('');
  setToolsError(state.layoutTools ? null : TOOL_MSG.TOOL_MISSING);
}

function viewerUrl() {
  return `/viewer.html#doc=${encodeURIComponent(state.docToken)}`;
}

// 클릭 처리기 안에서 곧바로(await 앞에서) 창을 연다 — 그래야 팝업 차단에 걸리지 않는다.
function viewText() {
  if (!state.doc || !state.file || !state.docToken || !state.layoutTools) return;
  setToolsError(null);
  const url = viewerUrl();
  let win = null;
  try { win = window.open(url, '_blank'); } catch { win = null; }
  if (!win) {
    setToolsStatus('');
    setToolsError(['새 창이 막혔어요. 주소창의 팝업 허용을 누르거나 아래 링크로 여세요.'], url);
    announce('새 창이 막혔어요. 아래 링크로 여세요.');
    return;
  }
  setToolsStatus('새 창에서 추출한 텍스트를 열었어요.');
}

// 보기 창의 요청에 답한다: {type:'need-doc', token} → 같은 문서면 File을, 아니면 no-doc.
// File은 구조적 복제로 넘어간다(chromium·Firefox에서 실측 — tools/check_viewer.py).
let channel = null;
function setupChannel() {
  if (typeof BroadcastChannel !== 'function') return;
  channel = new BroadcastChannel(VIEWER_CHANNEL);
  channel.onmessage = (e) => {
    const m = e.data;
    if (!m || typeof m !== 'object' || m.type !== 'need-doc' || typeof m.token !== 'string') return;
    if (state.docToken && m.token === state.docToken && state.file && state.doc) {
      channel.postMessage({ type: 'doc', token: m.token, file: state.file,
        fileName: state.file.name || '', title: docTitle(state.doc.meta) });
    } else {
      channel.postMessage({ type: 'no-doc', token: m.token });
    }
  };
}

function stopDownload(abort) {
  const d = state.download;
  if (!d) return;
  clearInterval(d.ticker);
  clearTimeout(d.timer);
  state.download = null;
  if (abort) d.controller.abort();
  render();
}

function toolCode(code) {
  return code && Object.prototype.hasOwnProperty.call(TOOL_MSG, code) ? code : 'UNKNOWN';
}

async function downloadText() {
  if (state.download || !state.doc || !state.file || !state.layoutTools) return;
  const file = state.file;
  const name = exportFileName(file.name);
  const d = { token: state.docToken, controller: new AbortController(), startedAt: Date.now(), ticker: 0, timer: 0, timedOut: false };
  d.timer = setTimeout(() => { d.timedOut = true; d.controller.abort(); }, TIMEOUTS.export);
  state.download = d;
  render();
  setToolsError(null);
  const tick = () => setToolsStatus(`다운로드 파일을 만들고 있어요… ${Math.floor((Date.now() - d.startedAt) / 1000)}초`);
  tick();
  d.ticker = setInterval(tick, 1000);
  announce('다운로드 파일을 만들고 있어요.');
  const current = () => state.download === d;
  try {
    let res;
    try {
      res = await fetch('/api/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/pdf', 'X-Filename': encodeURIComponent(file.name || '') },
        body: file,
        signal: d.controller.signal,
      });
    } catch {
      throw new ApiError(d.timedOut ? 'CLIENT_TIMEOUT' : d.controller.signal.aborted ? 'ABORTED' : 'NETWORK');
    }
    if (!res.ok) {
      let code = null;
      try {
        const body = await res.json();
        if (body && body.error && typeof body.error.code === 'string') code = body.error.code;
      } catch { /* JSON이 아닌 오류 응답 */ }
      if (!code) code = res.status === 409 ? 'BUSY' : res.status === 504 ? 'EXTRACT_TIMEOUT' : 'UNKNOWN';
      throw new ApiError(code);
    }
    let blob;
    try { blob = await res.blob(); } catch {
      throw new ApiError(d.timedOut ? 'CLIENT_TIMEOUT' : d.controller.signal.aborted ? 'ABORTED' : 'NETWORK');
    }
    if (!current()) return;
    const omitted = parseInt(res.headers.get('X-Figures-Omitted') || '0', 10);
    saveBlob(blob, name);
    const hadFocus = document.activeElement === el.downloadText;
    stopDownload(false);
    if (hadFocus || document.activeElement === document.body) el.downloadText.focus();
    const msg = `${name}을(를) 내려받았어요.` + (omitted > 0 ? ` 그림 ${num(omitted)}개는 크기·시간 제한으로 빠졌어요.` : '');
    setToolsStatus(msg);
  } catch (e) {
    if (!current() || e.code === 'ABORTED') return;
    const hadFocus = document.activeElement === el.downloadText || document.activeElement === document.body;
    stopDownload(false);
    if (hadFocus) el.downloadText.focus();
    setToolsStatus('');
    setToolsError(TOOL_MSG[toolCode(e.code)]);
  }
}

// Blob을 <a download>로 저장한다. 주소는 10초 뒤 돌려준다(저장이 시작될 시간).
function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.hidden = true;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

// ── 첫 로드: 상태 확인 ──────────────────────────────────
async function checkHealth() {
  const notices = [];
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), TIMEOUTS.health);
    const res = await fetch('/api/health', { signal: ctrl.signal, cache: 'no-store' });
    clearTimeout(t);
    const h = await res.json();
    if (!res.ok || !h || typeof h !== 'object') throw new Error('health');
    const tools = h.tools && typeof h.tools === 'object' ? h.tools : {};
    if (Object.values(tools).some((v) => v === false)) notices.push(['PDF 읽기 도구 없음', MSG.TOOL_MISSING]);
    // 보기·내려받기 도구(SPEC §10.3): 없어도 추출·요약은 되므로 미리 안내 대신 두 버튼 옆에만 알린다.
    const lt = h.layout_tools && typeof h.layout_tools === 'object' ? h.layout_tools : {};
    state.layoutTools = !Object.values(lt).some((v) => v === false);
    if (!state.layoutTools) {
      setToolsError(TOOL_MSG.TOOL_MISSING);
      render();
    }
    if (h.key === false) {
      notices.push(['요약 설정 없음', ['PDF는 읽을 수 있지만 요약은 할 수 없어요. ', ...MSG.NO_KEY]]);
    }
  } catch {
    notices.push(['서버 연결 안 됨', MSG.NETWORK]);
  }
  el.health.replaceChildren(...notices.map(([title, parts]) => {
    const box = document.createElement('div');
    box.className = 'notice notice--warn';
    box.setAttribute('role', 'status');
    const strong = document.createElement('p');
    strong.className = 'notice__title';
    strong.textContent = title;
    const body = document.createElement('div');
    fillMessage(body, parts);
    box.append(strong, body);
    return box;
  }));
}

// ── 이벤트 ──────────────────────────────────────────────
function onClick(e) {
  const t = e.target.closest('[data-action]');
  if (!t || !el.main.contains(t) || t.disabled) return;
  switch (t.dataset.action) {
    case 'pick': if (!el.drop.classList.contains('is-disabled')) openPicker(); break;
    case 'summarize': summarize(); break;
    case 'cancel': cancel(); break;
    case 'retry': retry(); break;
    case 'another': another(); break;
    case 'copy': copy(); break;
    case 'view-text': viewText(); break;
    case 'download-text': downloadText(); break;
  }
}

// 드래그 중인 것이 파일인지(글자·링크 드래그는 무시)
const hasFiles = (e) => !!e.dataTransfer && [...(e.dataTransfer.types || [])].includes('Files');

function setupDrop() {
  let depth = 0;   // 안쪽 요소를 지날 때 생기는 dragenter/dragleave 짝을 센다
  const zone = el.drop;
  zone.addEventListener('dragenter', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth += 1;
    if (!zone.classList.contains('is-disabled')) zone.classList.add('is-over');
  });
  zone.addEventListener('dragover', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = zone.classList.contains('is-disabled') ? 'none' : 'copy';
  });
  zone.addEventListener('dragleave', () => {
    depth = Math.max(0, depth - 1);
    if (!depth) zone.classList.remove('is-over');
  });
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    depth = 0;
    zone.classList.remove('is-over');
    if (zone.classList.contains('is-disabled') || !e.dataTransfer) return;
    const items = [...(e.dataTransfer.items || [])].filter((i) => i.kind === 'file');
    const folder = items.some((i) => i.webkitGetAsEntry?.()?.isDirectory);
    acceptFiles([...(e.dataTransfer.files || [])], folder);
  });
  // 영역 밖에 놓아도 브라우저가 PDF를 열어 페이지를 떠나지 않게 막는다(SPEC §7.1).
  window.addEventListener('dragover', (e) => {
    if (zone.contains(e.target)) return;
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'none';
  });
  window.addEventListener('drop', (e) => { if (!zone.contains(e.target)) e.preventDefault(); });
}

function init() {
  if (location.protocol === 'file:') {
    // 키가 서버에만 있어야 하므로 파일로 열면 요약할 수 없다 — 안내만 보인다(SPEC §2).
    el.boot.classList.add('is-shown');
    return;
  }
  document.documentElement.classList.add('app-ready');
  el.main.addEventListener('click', onClick);
  el.input.addEventListener('change', () => {
    const files = [...(el.input.files || [])];
    el.input.value = '';
    acceptFiles(files);
  });
  setupDrop();
  setupChannel();
  render();
  checkHealth();
}

init();
