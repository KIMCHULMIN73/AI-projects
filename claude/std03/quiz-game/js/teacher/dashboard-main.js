// [선생님 모드 · 5단계] 대시보드 부팅 & 배선.
//
// analysis.json을 읽어 뷰에 넘기고, 정렬·검색·선택을 잇는다.
// **여기서도 통계를 계산하지 않는다** — 정렬 순서를 바꾸는 것까지가 전부다.
// 새 수치가 필요하면 tools/results_stats.py나 results_compare.py에 추가할 것.

import { CATEGORY_ORDER } from '../../data/questions.js';
import { DashboardViews } from './dashboard-views.js';

const ANALYSIS_URL = 'data/results/analysis.json';
const EXPECTED_SCHEMA = 'quiz-analysis/v1';

const $ = (sel) => document.querySelector(sel);

const state = {
  data: null,
  sortKey: 'rank',
  sortAsc: true,
  query: '',
  pickedId: null,
};

/** 정렬 키 → 그 참가자에서 뽑아낼 값. null은 항상 뒤로 보낸다. */
function valueOf(p, key) {
  if (key === 'cat0' || key === 'cat1' || key === 'cat2' || key === 'cat3') {
    const cat = CATEGORY_ORDER[Number(key.slice(3))];
    const v = p.byCategory[cat];
    return v === undefined ? null : v;
  }
  return p[key];
}

function visibleRows() {
  const q = state.query.trim().toLowerCase();
  const rows = state.data.participants.filter(
    (p) => q === '' || p.label.toLowerCase().includes(q)
  );
  const dir = state.sortAsc ? 1 : -1;
  return rows.slice().sort((a, b) => {
    const x = valueOf(a, state.sortKey);
    const y = valueOf(b, state.sortKey);
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    if (typeof x === 'string') return x.localeCompare(y, 'ko') * dir;
    return (x - y) * dir;
  });
}

function catMeanMap() {
  const map = {};
  state.data.categories.forEach((c) => {
    map[c.key] = c.meanAccuracy;
  });
  return map;
}

function pickedParticipant() {
  if (state.pickedId === null) return null;
  return state.data.participants.find((p) => p.id === state.pickedId) || null;
}

function renderTable() {
  DashboardViews.showRanking(visibleRows(), catMeanMap(), state.pickedId);
  document.querySelectorAll('#rankHead th').forEach((th) => {
    if (th.dataset.sort === state.sortKey) {
      th.setAttribute('aria-sort', state.sortAsc ? 'ascending' : 'descending');
    } else {
      th.removeAttribute('aria-sort');
    }
  });
}

function renderAll() {
  const a = state.data;
  DashboardViews.showSummary(a);
  renderTable();
  DashboardViews.showHistogram(a, pickedParticipant());
  DashboardViews.showCategories(a);
  DashboardViews.showDifficulties(a);
  DashboardViews.showItems(a);
}

function bind() {
  // 줄은 매번 새로 그려지므로 개별 바인딩은 유지되지 않는다 — 위임으로 한 번만 건다.
  $('#rankBody').addEventListener('click', (event) => {
    const row = event.target.closest('tr[data-participant-id]');
    if (!row) return;
    const id = row.dataset.participantId;
    state.pickedId = state.pickedId === id ? null : id;
    renderTable();
    DashboardViews.showHistogram(state.data, pickedParticipant());
    const picked = pickedParticipant();
    if (picked) DashboardViews.showDetail(picked, state.data);
    else DashboardViews.hideDetail();
  });

  $('#rankHead').addEventListener('click', (event) => {
    const th = event.target.closest('th[data-sort]');
    if (!th) return;
    const key = th.dataset.sort;
    if (state.sortKey === key) {
      state.sortAsc = !state.sortAsc;
    } else {
      state.sortKey = key;
      // 석차·라벨은 작은 쪽이 위, 나머지 수치는 큰 쪽이 위가 자연스럽다.
      state.sortAsc = key === 'rank' || key === 'label';
    }
    renderTable();
  });

  $('#searchBox').addEventListener('input', (event) => {
    state.query = event.target.value;
    renderTable();
  });

  $('#detailClose').addEventListener('click', () => {
    state.pickedId = null;
    DashboardViews.hideDetail();
    renderTable();
    DashboardViews.showHistogram(state.data, null);
  });
}

async function boot() {
  let response;
  try {
    response = await fetch(ANALYSIS_URL, { cache: 'no-store' });
  } catch (error) {
    DashboardViews.fatal(
      `${ANALYSIS_URL}을 읽지 못했습니다. 로컬 서버(./run.sh)로 열었는지 확인하세요.`
    );
    return;
  }
  if (!response.ok) {
    DashboardViews.fatal(
      `아직 분석 결과가 없습니다(${response.status}). ` +
      '먼저 /teacher-collect 로 결과를 모으고 /teacher-stats 를 돌리세요.'
    );
    return;
  }

  let data;
  try {
    data = await response.json();
  } catch (error) {
    DashboardViews.fatal(`${ANALYSIS_URL}이 올바른 JSON이 아닙니다.`);
    return;
  }

  // 스키마가 다르면 조용히 잘못 그리는 것보다 알리고 그리는 편이 낫다.
  if (data.schema !== EXPECTED_SCHEMA) {
    DashboardViews.fatal(
      `스키마가 ${data.schema} 입니다(${EXPECTED_SCHEMA} 예상). ` +
      '화면이 일부 항목을 제대로 그리지 못할 수 있습니다.'
    );
  }

  state.data = data;
  bind();
  renderAll();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
