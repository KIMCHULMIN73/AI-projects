// [선생님 모드 · 1단계] 성적 반출 — localStorage에 갇힌 기록을 파일로 꺼낸다.
//
// 게임 코드는 이 파일을 import 하지 않는다. 반대로 이 파일이 모듈1(storage.js)을
// 읽는다. 지워도 게임은 그대로 돈다(tools/·preview.html과 같은 규율).
//
// **이벤트를 직접 바인딩하지 않는다** — 배선은 main.js가 독점한다.
// 화면에 알리는 일도 main.js가 한다. 여기서는 결과 객체만 돌려준다.

import { Storage } from '../storage.js';

/** 반출 파일 스키마. 선생님 모드 2~5단계가 전부 이 계약을 읽는다. */
const EXPORT_SCHEMA = 'quiz-export/v2';

/**
 * 파일명에 쓸 수 없는 문자를 걷어낸다.
 * 이름은 시작 화면에서 자유 입력한 문자열이라 `../`나 경로 구분자가 들어올 수 있다.
 */
function safeName(name) {
  const cleaned = String(name || '도전자')
    .replace(/[/\\:*?"<>|]/g, '_')
    .replace(/[\u0000-\u001f\u007f]/g, '')
    .replace(/\.+/g, '.')
    .trim();
  return cleaned || '도전자';
}

/** 파일명용 타임스탬프. YYYYMMDD-HHmm. */
function stamp(date = new Date()) {
  const p = (n) => String(n).padStart(2, '0');
  return (
    `${date.getFullYear()}${p(date.getMonth() + 1)}${p(date.getDate())}` +
    `-${p(date.getHours())}${p(date.getMinutes())}`
  );
}

export const TeacherExport = {
  /**
   * 반출 객체를 만든다. 이름을 주면 그 사람 기록만 담는다.
   *
   * 기록은 **저장된 순서 그대로** 담는다(getAllRecords). 정렬은 분석 쪽 몫이다.
   * playedAt은 절대 다시 찍지 않는다 — 그게 기록의 신원이고, 중복 판정이
   * `name + playedAt`에 걸려 있어서 새로 찍으면 같은 판이 몇 번이고 다시 들어간다.
   */
  buildExport(name) {
    const all = Storage.getAllRecords();
    const wanted = name ? all.filter((r) => r && r.name === name) : all;
    return {
      schema: EXPORT_SCHEMA,
      app: 'std03-quiz',
      exportedAt: new Date().toISOString(),
      exportedBy: name || (wanted.length ? wanted[wanted.length - 1].name : ''),
      records: wanted,
    };
  },

  /**
   * 반출 객체를 JSON 파일로 내려받게 한다.
   * @returns {{ok: boolean, filename?: string, count?: number, error?: string}}
   */
  downloadExport(name) {
    const payload = this.buildExport(name);
    if (payload.records.length === 0) {
      return { ok: false, error: '내보낼 기록이 없습니다.' };
    }

    const filename = `quiz-result-${safeName(payload.exportedBy)}-${stamp()}.json`;
    let url = '';
    try {
      const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: 'application/json',
      });
      url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.append(a);
      a.click();
      a.remove();
      return { ok: true, filename, count: payload.records.length };
    } catch (error) {
      return { ok: false, error: (error && error.message) || '내보내기 실패' };
    } finally {
      // objectURL이 판마다 새면 곤란하다. 성공/실패와 무관하게 회수한다.
      // 클릭이 처리될 틈을 주려고 다음 틱으로 미룬다.
      if (url) setTimeout(() => URL.revokeObjectURL(url), 0);
    }
  },

  /**
   * 같은 내용을 클립보드로. 다운로드가 막힌 환경의 대비책이다.
   *
   * **실패를 조용히 삼키지 않는다.** 반출 실패를 모르고 넘어가면 그 사람 성적이
   * 통째로 사라진다(소리·직전판 저장이 실패를 삼키는 것과 정반대의 이유다).
   * @returns {Promise<{ok: boolean, count?: number, error?: string}>}
   */
  async copyExport(name) {
    const payload = this.buildExport(name);
    if (payload.records.length === 0) {
      return { ok: false, error: '내보낼 기록이 없습니다.' };
    }
    const text = JSON.stringify(payload, null, 2);
    try {
      if (!navigator.clipboard || !navigator.clipboard.writeText) {
        throw new Error('이 브라우저에서는 클립보드를 쓸 수 없습니다');
      }
      await navigator.clipboard.writeText(text);
      return { ok: true, count: payload.records.length };
    } catch (error) {
      return {
        ok: false,
        error: `${(error && error.message) || '복사 실패'} — 내보내기 버튼을 쓰세요`,
      };
    }
  },
};
