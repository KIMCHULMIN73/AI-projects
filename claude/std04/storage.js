// localStorage 접근은 전부 이 파일을 거친다. (→ units/UNIT_storage.md)
//
// localStorage는 시크릿 모드·용량 초과·브라우저 설정으로 그냥 던진다. 여기서 모두
// 잡아서 앱이 죽지 않게 한다. 실패를 사용자에게 알릴지는 **부르는 쪽이 정한다** —
// 1단계 캐시는 조용히 넘어가고, 3단계 보관함은 반드시 알린다.

// 같은 127.0.0.1 출처를 쓰는 형제 프로젝트(std02의 todos-app-v1 등)와 섞이지 않게 접두사를 붙인다.
// v1은 데이터 모양이 바뀔 때 올린다. 올려도 옛 키는 지우지 않는다.
const PREFIX = 'fridge.v1.';

export const KEYS = Object.freeze({
  lastIngredients: `${PREFIX}lastIngredients`,   // 1단계: 마지막 분석 결과(IngredientList)
});

/** 읽기. 없음·차단·깨진 JSON 모두 fallback으로 돌려준다. */
export function readJSON(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}

/** 쓰기. 던지지 않고 `{ ok, quota }`를 돌려준다. quota=true면 용량 초과다. */
export function writeJSON(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return { ok: true, quota: false };
  } catch (err) {
    return { ok: false, quota: isQuotaError(err) };
  }
}

function isQuotaError(err) {
  return err instanceof DOMException
    && (err.name === 'QuotaExceededError' || err.name === 'NS_ERROR_DOM_QUOTA_REACHED' || err.code === 22);
}
