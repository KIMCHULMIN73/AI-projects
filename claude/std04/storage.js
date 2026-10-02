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
  profiles: `${PREFIX}profiles`,                 // 3단계: 프로필 배열
  activeProfile: `${PREFIX}activeProfile`,       // 3단계: 지금 선택된 프로필 id
  saved: (profileId) => `${PREFIX}saved.${profileId}`,   // 3단계: 그 프로필의 보관함
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
    if (!isQuotaError(err)) return { ok: false, quota: false };
    // QuotaExceededError 라고 다 "용량이 찬" 것은 아니다 — 아래 isBlocked() 참고.
    return { ok: false, quota: !isBlocked() };
  }
}

/** 저장이 **막혀** 있는가(용량이 찬 것이 아니라).
 *
 * 저장을 막는 브라우저(사파리 프라이빗 브라우징 등)는 용량을 0으로 두기 때문에
 * 용량 초과와 **똑같은 `QuotaExceededError`**를 던진다. 둘을 가르지 않으면 3단계가
 * "오래된 레시피를 지워 주세요"라고 **지울 것이 하나도 없는 사용자에게 틀린 지시**를
 * 하고, `PRD_step3`가 정한 "저장 차단이면 프로필은 메모리에서라도 쓰게 한다" 경로도
 * 타지 못한다.
 *
 * 가르는 기준은 "지울 것이 있느냐"다. 저장소가 비어 있는데 1바이트도 안 들어가면
 * 지워서 해결할 수 있는 상태가 아니다 — 그건 찬 것이 아니라 막힌 것이다.
 *
 * 주의: 이 기기에 사파리가 없어 **실제 사파리로는 확인하지 못했다.** 크로미움
 * 시크릿 창은 저장이 되므로 재현되지 않는다.
 */
function isBlocked() {
  try {
    if (localStorage.length > 0) return false;   // 들어 있는 것이 있다 = 쓸 수는 있었다
    const probe = `${PREFIX}__probe`;
    localStorage.setItem(probe, '1');            // 빈 저장소에 1바이트
    localStorage.removeItem(probe);
    return false;                                // 들어갔다 = 아까 실패는 값이 커서다
  } catch {
    return true;                                 // 빈 저장소에 1바이트도 안 들어간다 = 막혔다
  }
}

/** 지우기. 던지지 않고 성공 여부를 돌려준다. */
export function removeKey(key) {
  try {
    localStorage.removeItem(key);
    return true;
  } catch {
    return false;
  }
}

function isQuotaError(err) {
  return err instanceof DOMException
    && (err.name === 'QuotaExceededError' || err.name === 'NS_ERROR_DOM_QUOTA_REACHED' || err.code === 22);
}
