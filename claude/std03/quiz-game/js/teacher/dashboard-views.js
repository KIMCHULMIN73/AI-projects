// [선생님 모드 · 5단계] 대시보드 렌더 — **그리기만 한다.**
//
// 평균도 석차도 변별도도 여기서 구하지 않는다. analysis.json에 이미 들어 있는
// 값을 화면에 올릴 뿐이다. 화면이 수를 다시 만들면 터미널과 화면이 서로 다른
// 1등을 말할 수 있다(모듈2의 views.js가 지키는 규율과 같은 이유다).
//
// 여기서 해도 되는 것: 정렬·필터·표기 변환·색과 길이 매핑.
// 하면 안 되는 것: 평균/표준편차/z/백분위 계산, 석차 매기기, 판정.
//
// **이벤트를 바인딩하지 않는다** — 줄에 data-participant-id만 달고,
// 배선은 dashboard-main.js가 위임으로 한 번만 건다.
//
// 모든 텍스트는 textContent로 넣는다. 라벨은 사람이 시작 화면에 직접 타이핑한
// 문자열에서 나왔으므로, HTML 문자열로 조립해 주입하면 남의 입력이 이 화면에서
// 그대로 실행된다. views.js가 같은 이유로 지키는 규칙이고 여기서는 더 엄하다.

import { CATEGORIES, CATEGORY_ORDER } from '../../data/questions.js';

const $ = (sel) => document.querySelector(sel);

/** 분야 라벨의 유일한 출처는 CATEGORIES다. 화면에 문자열을 하드코딩하지 않는다. */
function catLabel(key) {
  const meta = CATEGORIES[key];
  return meta ? meta.label : key;
}

/** 못 구한 값(null)은 0이 아니라 —로 그린다. 둘은 전혀 다른 뜻이다. */
function num(v, digits) {
  if (v === null || v === undefined) return '—';
  return Number(v).toFixed(digits === undefined ? 0 : digits);
}

function pct(v, digits) {
  if (v === null || v === undefined) return '—';
  return `${(Number(v) * 100).toFixed(digits === undefined ? 1 : digits)}%`;
}

function signed(v, digits) {
  if (v === null || v === undefined) return '—';
  const d = digits === undefined ? 2 : digits;
  return (Number(v) >= 0 ? '+' : '') + Number(v).toFixed(d);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function timeText(iso) {
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return '—';
  const p = (n) => String(n).padStart(2, '0');
  return `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())} ` +
         `${p(t.getHours())}:${p(t.getMinutes())}`;
}

export const DashboardViews = {
  /** 상단 요약 띠. 참가 단위와 생성 시각을 반드시 함께 보인다. */
  showSummary(a) {
    const o = a.overall;
    const c = a.coverage;
    $('#summaryLine').textContent =
      `참가 ${c.participants}명 (서로 다른 이름 ${c.distinctNames}개) · ` +
      `평균 ${num(o.mean, 1)} · 표준편차 ${num(o.stdev, 1)} · ` +
      `중앙값 ${num(o.median, 0)} · 최고 ${o.max} / 최저 ${o.min}`;
    $('#generatedChip').textContent = `생성: ${timeText(a.generatedAt)}`;
    $('#anonChip').hidden = a.anonymized !== true;

    CATEGORY_ORDER.forEach((key, i) => {
      const th = document.getElementById(`th-cat${i}`);
      if (th) th.textContent = catLabel(key).replace(' 상식', '');
    });
  },

  /**
   * 순위표. 정렬·필터는 main.js가 넘겨 준 배열 순서를 그대로 따른다.
   * 분야 칸은 전체 평균 대비 위/아래를 색과 막대로 함께 보이되 숫자를 늘 곁들인다.
   */
  showRanking(rows, catMean, pickedId) {
    const body = $('#rankBody');
    if (rows.length === 0) {
      const tr = el('tr');
      const td = el('td', 'empty', '조건에 맞는 참가자가 없습니다.');
      td.colSpan = 10;
      tr.append(td);
      body.replaceChildren(tr);
      return;
    }

    body.replaceChildren(...rows.map((p) => {
      const tr = el('tr');
      tr.dataset.participantId = p.id;
      if (p.id === pickedId) tr.classList.add('is-picked');

      tr.append(
        el('td', 'num', p.rank),
        el('td', null, p.label),
        el('td', 'num', p.score),
        el('td', 'num', pct(p.accuracy)),
        el('td', 'num', signed(p.z)),
        el('td', 'num', p.percentile === null ? '—' : num(p.percentile, 0))
      );

      CATEGORY_ORDER.forEach((key) => {
        const v = p.byCategory[key];
        const td = el('td', 'cell-cat num');
        if (v === undefined || v === null) {
          td.textContent = '—';
          tr.append(td);
          return;
        }
        const base = catMean[key];
        if (base !== null && base !== undefined) {
          td.classList.add(v >= base ? 'is-above' : 'is-below');
        }
        td.append(el('span', null, pct(v, 0)));
        const bar = el('span', 'cat-bar');
        bar.style.width = `${Math.round(Number(v) * 100)}%`;
        td.append(bar);
        tr.append(td);
      });
      return tr;
    }));
  },

  /**
   * 점수 분포. 구간은 analysis.json에 있는 것을 그대로 쓴다 — 화면이 다시 나누지 않는다.
   * 평균 자리와 ±1σ 띠를 표시하고, 고른 참가자가 있으면 그 칸에 ▲를 세운다.
   */
  showHistogram(a, picked) {
    const o = a.overall;
    const bins = o.histogram;
    const counts = bins.map((b) => b.count);
    const top = Math.max(...counts, 1);
    const lo = o.mean - o.stdev;
    const hi = o.mean + o.stdev;

    const box = $('#histogram');
    box.replaceChildren(...bins.map((b) => {
      const row = el('div', 'hist-row');
      const inBand = b.to >= lo && b.from <= hi;
      const hasMean = o.mean >= b.from && o.mean <= b.to;
      const hasMe = picked !== null && picked !== undefined &&
                    picked.score >= b.from && picked.score <= b.to;

      row.append(el('div', 'hist-label', `${b.from} ~ ${b.to}`));
      const track = el('div', `hist-track${inBand ? ' hist-band' : ''}`);
      const bar = el('div', 'hist-bar');
      bar.style.width = `${Math.round((b.count / top) * 100)}%`;
      track.append(bar);
      row.append(track);

      let tail = String(b.count);
      if (hasMean) tail += ' ◀평균';
      row.append(el('div', `hist-count${hasMe ? ' hist-mark' : ''}`,
                    hasMe ? `${tail} ▲` : tail));
      return row;
    }));

    const legend = el('div', 'hist-legend',
      `옅은 띠 = 평균 ±1 표준편차 (${num(lo, 0)} ~ ${num(hi, 0)})` +
      (picked ? ` · ▲ ${picked.label} ${picked.score}점 (z ${signed(picked.z)}, ` +
                `백분위 ${picked.percentile === null ? '—' : num(picked.percentile, 0)})` : ''));
    box.append(legend);
  },

  /** 분야별 평균 정답률. 가장 약한 분야를 굵게. */
  showCategories(a) {
    const known = a.categories.filter((c) => c.meanAccuracy !== null);
    let weakest = null;
    known.forEach((c) => {
      if (weakest === null || c.meanAccuracy < weakest.meanAccuracy) weakest = c;
    });

    $('#categoryList').replaceChildren(...a.categories.map((c) => {
      const row = el('div', 'bar-row');
      if (weakest && c.key === weakest.key) row.classList.add('is-weakest');
      row.append(el('div', 'bar-name', catLabel(c.key)));
      const track = el('div', 'bar-track');
      if (c.meanAccuracy !== null) {
        const fill = el('div', 'bar-fill');
        fill.style.width = `${Math.round(c.meanAccuracy * 100)}%`;
        track.append(fill);
      }
      row.append(track);
      row.append(el('div', 'bar-val',
        `${pct(c.meanAccuracy)}  ${signed(c.vsOverall, 3)}`));
      return row;
    }));
  },

  /** 난이도 검증 줄. 계단이 흐트러진 자리를 붉게. */
  showDifficulties(a) {
    $('#difficultyList').replaceChildren(...a.difficulties.map((d) => {
      const row = el('div', 'bar-row');
      if (d.inverted) row.classList.add('is-inverted');
      row.append(el('div', 'bar-name', d.label));
      const track = el('div', 'bar-track');
      if (d.actualAccuracy !== null) {
        const fill = el('div', 'bar-fill');
        fill.style.width = `${Math.round(d.actualAccuracy * 100)}%`;
        track.append(fill);
      }
      row.append(track);
      row.append(el('div', 'bar-val',
        d.actualAccuracy === null
          ? `— (응답 ${d.n}건)`
          : `${pct(d.actualAccuracy)} (${d.n}건)`));
      return row;
    }));
  },

  /** 한 참가자로 파고든다. 전체 평균을 나란히 두어 위치가 보이게 한다. */
  showDetail(p, a) {
    $('#detailTitle').textContent = `${p.label} — 상세`;

    const left = el('div');
    left.append(el('h3', null, '분야별 정답률 (괄호는 전체 평균)'));
    const dl1 = el('dl');
    a.categories.forEach((c) => {
      const mine = p.byCategory[c.key];
      dl1.append(el('dt', null, catLabel(c.key)));
      dl1.append(el('dd', null,
        `${pct(mine === undefined ? null : mine)}  (${pct(c.meanAccuracy)})`));
    });
    left.append(dl1);

    const right = el('div');
    right.append(el('h3', null, '난이도별 정답률 · 그 밖'));
    const dl2 = el('dl');
    if (p.byDifficulty === null) {
      dl2.append(el('dt', null, '난이도별'));
      dl2.append(el('dd', 'muted', '구버전 기록이라 문항별 응답이 없습니다'));
    } else {
      a.difficulties.forEach((d) => {
        dl2.append(el('dt', null, d.label));
        dl2.append(el('dd', null, pct(p.byDifficulty[d.key])));
      });
    }
    dl2.append(el('dt', null, '석차'));
    dl2.append(el('dd', null, `${p.rank}위 / ${a.coverage.participants}명`));
    dl2.append(el('dt', null, 'z-점수'));
    dl2.append(el('dd', null, signed(p.z)));
    dl2.append(el('dt', null, '백분위'));
    dl2.append(el('dd', null, p.percentile === null ? '—' : num(p.percentile, 0)));
    dl2.append(el('dt', null, '강점 / 약점'));
    dl2.append(el('dd', null,
      `${p.strongest === null ? '—' : catLabel(p.strongest)} / ` +
      `${p.weakest === null ? '—' : catLabel(p.weakest)}`));
    dl2.append(el('dt', null, '문항당 평균'));
    dl2.append(el('dd', null,
      p.meanElapsedMs === null ? '—' : `${num(p.meanElapsedMs / 1000, 1)}초`));
    right.append(dl2);

    $('#detailBody').replaceChildren(left, right);
    $('#detailCard').hidden = false;
  },

  hideDetail() {
    $('#detailCard').hidden = true;
  },

  /**
   * 재검토 후보 문항. **판정 가능 범위를 표 위에 먼저 적는다** —
   * 이 줄이 없으면 표가 실제보다 훨씬 정밀해 보인다.
   */
  showItems(a) {
    const cov = a.itemCoverage;
    const covText = `출제된 문항 ${cov.seen}개 중 판정 가능 ${cov.judged}개 ` +
                    `(최소 노출 ${cov.minN}회)`;
    $('#itemCoverage').textContent = covText;

    const flagged = a.items.filter((i) => i.flags.length > 0);
    const box = $('#itemList');
    if (flagged.length === 0) {
      const why = cov.judged === 0
        ? '아직 판정할 만큼 응답이 쌓이지 않았습니다. 결함이 없다는 뜻이 아니라 데이터가 모자란 것입니다.'
        : '플래그가 붙은 문항이 없습니다.';
      box.replaceChildren(el('p', 'empty', why));
      return;
    }

    const noteOf = {};
    a.flags.forEach((f) => {
      noteOf[f.id] = noteOf[f.id] ? `${noteOf[f.id]} / ${f.note}` : f.note;
    });
    const isRed = (i) => i.flags.some((f) => f === 'answer-suspect' || f === 'negative-D');
    const ordered = flagged.slice().sort((x, y) => (isRed(y) ? 1 : 0) - (isRed(x) ? 1 : 0));

    box.replaceChildren(...ordered.map((i) => {
      const card = el('div', 'item');
      const head = el('div', 'item-head');
      head.append(el('span', 'item-id', i.id));
      head.append(el('span', 'muted', `n=${i.n} · 정답률 ${pct(i.p)} · D ${signed(i.d)}`));
      i.flags.forEach((f) => {
        const red = f === 'answer-suspect' || f === 'negative-D';
        head.append(el('span', `tag ${red ? 'tag-red' : 'tag-warn'}`,
                       `${red ? '🔴' : '⚠️'} ${f}`));
      });
      card.append(head);
      card.append(el('p', 'item-q', i.question));

      const bars = el('div', 'choice-bars');
      const top = Math.max(...i.choiceCounts, 1);
      i.choiceCounts.forEach((count, idx) => {
        const row = el('div', 'choice-row');
        if (idx === i.answerIndex) row.classList.add('is-answer');
        row.append(el('div', 'bar-name',
          `보기${idx + 1}${idx === i.answerIndex ? ' (정답)' : ''}`));
        const track = el('div', 'bar-track');
        const fill = el('div', 'bar-fill');
        fill.style.width = `${Math.round((count / top) * 100)}%`;
        track.append(fill);
        row.append(track);
        row.append(el('div', 'bar-val', `${count}회`));
        bars.append(row);
      });
      card.append(bars);
      if (noteOf[i.id]) card.append(el('p', 'item-note', noteOf[i.id]));
      if (i.method) card.append(el('p', 'item-note muted', `변별도 산출: ${i.method} (근사)`));
      return card;
    }));
  },

  fatal(message) {
    const box = $('#fatal');
    box.hidden = false;
    box.textContent = message;
  },
};
