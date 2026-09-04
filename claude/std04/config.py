"""OpenRouter API 키를 안전한 순서로 찾아 돌려준다.

**키를 파일에서 직접 읽는 코드는 이 모듈 하나뿐이어야 한다.** 다른 곳에서 또
`.env`를 열기 시작하면 탐색 순서·권한 점검·마스킹이 갈라져서, 어느 한쪽만 고치는
사고가 난다.

찾는 순서 (안전한 것부터):

1. 환경변수 `OPENROUTER_API_KEY` — 디스크에 남지 않는다. 가장 안전하다.
2. `~/.config/openrouter/env` — 저장소 **밖**의 전용 파일. 평소에는 이걸 쓴다.
3. `std04/.env` — 저장소 **안**. 있으면 동작은 하되 경고한다(아래 참고).

3번을 지우지 않고 폴백으로 남겨 둔 이유: 다른 사람이 예전 방식대로 `.env`를
만들어 두었을 때 "키가 없다"고 죽는 대신 동작하면서 위치를 지적해 주는 편이
낫기 때문이다. 저장소 안의 파일은 `.gitignore` 한 줄에만 기대게 되고,
정적 서버를 띄우면 그대로 HTTP로 노출된다 — 그래서 경고 대상이다.
"""

from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path

ENV_VAR = "OPENROUTER_API_KEY"
SECRET_FILE = Path.home() / ".config" / "openrouter" / "env"
LOCAL_FALLBACK = Path(__file__).resolve().parent / ".env"
KEY_PREFIX = "sk-or-v1-"

# 로그·예외·화면 출력에서 키를 가릴 때 쓴다. 앞 4글자만 남긴다.
_KEY_RE = re.compile(r"sk-or-v1-[A-Za-z0-9]{4}[A-Za-z0-9]*")


class MissingKeyError(RuntimeError):
    """어디에서도 키를 찾지 못했다."""


def redact(text: object) -> str:
    """문자열 안의 OpenRouter 키를 가린다.

    예외를 찍거나 요청/응답을 로그로 남기기 전에 **반드시** 통과시킬 것.
    키가 통째로 터미널 스크롤백이나 로그 파일에 남는 사고가 가장 흔하다.
    """
    return _KEY_RE.sub(lambda m: m.group(0)[: len(KEY_PREFIX) + 4] + "…" + "*" * 6, str(text))


def _parse_env_file(path: Path) -> dict[str, str]:
    """`KEY=value` 꼴만 읽는 최소 파서.

    python-dotenv를 쓰지 않는다 — 이 리포지토리는 외부 의존성을 두지 않는 관례다.
    따옴표는 벗기고, `export ` 접두사와 `#` 주석은 무시한다.
    """
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        name, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name.strip()] = value
    return values


def _permission_warnings(path: Path) -> list[str]:
    """이 파일을 나 말고 누가 읽을 수 있는지 본다."""
    mode = path.stat().st_mode
    problems = []
    if mode & stat.S_IRGRP:
        problems.append("그룹")
    if mode & stat.S_IROTH:
        problems.append("다른 사용자")
    if not problems:
        return []
    return [
        f"{path} 를 {'·'.join(problems)}가 읽을 수 있습니다 (현재 {oct(mode & 0o777)[2:]}). "
        f"고치려면: chmod 600 {path}"
    ]


def find_api_key(*, allow_local: bool = True) -> tuple[str, str, list[str]]:
    """`(키, 출처 설명, 경고 목록)`을 돌려준다. 못 찾으면 MissingKeyError."""
    warnings: list[str] = []

    from_env = os.environ.get(ENV_VAR, "").strip()
    if from_env:
        return from_env, f"환경변수 {ENV_VAR}", warnings

    candidates: list[tuple[Path, str, bool]] = [(SECRET_FILE, "저장소 밖 전용 파일", False)]
    if allow_local:
        candidates.append((LOCAL_FALLBACK, "저장소 안 .env (권장하지 않음)", True))

    for path, label, inside_repo in candidates:
        if not path.is_file():
            continue
        value = _parse_env_file(path).get(ENV_VAR, "").strip()
        if not value:
            continue
        warnings += _permission_warnings(path)
        if inside_repo:
            warnings.append(
                f"키를 저장소 안({path})에서 읽었습니다. .gitignore 한 줄에만 기대게 되고 "
                f"정적 서버를 띄우면 HTTP로 노출됩니다. {SECRET_FILE} 로 옮기세요."
            )
        return value, f"{label}: {path}", warnings

    raise MissingKeyError(
        f"{ENV_VAR} 를 찾지 못했습니다. 확인한 곳:\n"
        f"  - 환경변수 {ENV_VAR}\n"
        f"  - {SECRET_FILE}\n"
        f"  - {LOCAL_FALLBACK}\n"
        f"설정 방법은 .env.example 을 보세요."
    )


def get_api_key(*, allow_local: bool = True, quiet: bool = False) -> str:
    """키만 돌려준다. 경고는 stderr로 흘린다(표준출력을 더럽히지 않는다)."""
    key, _source, warnings = find_api_key(allow_local=allow_local)
    if not quiet:
        for w in warnings:
            print(f"[경고] {w}", file=sys.stderr)
    return key


def auth_header(**extra: str) -> dict[str, str]:
    """OpenRouter 호출용 헤더. 키를 문자열로 조립해 돌아다니게 두지 않으려는 것."""
    headers = {"Authorization": f"Bearer {get_api_key()}", "Content-Type": "application/json"}
    headers.update(extra)
    return headers


if __name__ == "__main__":
    # 진단용. 키 자체는 절대 찍지 않는다.
    try:
        key, source, warnings = find_api_key()
    except MissingKeyError as exc:
        print(redact(exc), file=sys.stderr)
        raise SystemExit(1)
    ok = key.startswith(KEY_PREFIX)
    print(f"출처   : {source}")
    print(f"길이   : {len(key)}자")
    print(f"형식   : {'sk-or-v1- 로 시작 (정상)' if ok else '!! sk-or-v1- 로 시작하지 않음'}")
    print(f"마스킹 : {redact(key)}")
    for w in warnings:
        print(f"[경고] {w}", file=sys.stderr)
    raise SystemExit(0 if ok and not warnings else 1)
