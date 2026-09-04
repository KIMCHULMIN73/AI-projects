#!/usr/bin/env python3
"""키가 새어 나갈 만한 곳을 훑는 점검기. 앱은 이 파일을 쓰지 않는다(개발 전용).

    python3 tools/check_secrets.py            # 로컬 점검만 (네트워크 안 씀)
    python3 tools/check_secrets.py --online   # + OpenRouter에 키가 살아있는지 확인

커밋 전, 그리고 파일을 새로 만든 뒤에 돌린다.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

PROJECT = Path(config.__file__).resolve().parent
REPO = Path(
    subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=PROJECT, capture_output=True, text=True, check=True
    ).stdout.strip()
)

failures: list[str] = []
notes: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)


def is_ignored(relpath: str) -> bool:
    """`git check-ignore`는 부정(!) 규칙에 걸려도 종료코드 0을 준다.

    그래서 종료코드가 아니라 **어떤 패턴에 걸렸는지**를 봐야 한다.
    `!.env.example` 에 걸린 것은 '제외되지 않음'이라는 뜻이다.
    """
    out = git("check-ignore", "-v", "--no-index", relpath).stdout.strip()
    if not out:
        return False
    pattern = out.split("\t", 1)[0].rsplit(":", 1)[-1]
    return not pattern.startswith("!")


# 1. 키를 읽을 수 있는가, 어디서 읽는가
try:
    key, source, warnings = config.find_api_key()
except config.MissingKeyError as exc:
    check("키를 찾을 수 있다", False, config.redact(exc).splitlines()[0])
    print("\n키부터 설정하세요 — .env.example 참고.", file=sys.stderr)
    raise SystemExit(1)

check("키를 찾을 수 있다", True, source)
check("키 형식이 OpenRouter 것이다", key.startswith(config.KEY_PREFIX), f"{len(key)}자")
check("키가 저장소 밖에 있다", str(PROJECT) not in source, source)
for w in warnings:
    check("권한/위치 경고 없음", False, w)
if not warnings:
    check("권한/위치 경고 없음", True)

# 2. 저장소 어디에도 키 문자열이 없어야 한다
tracked = git("grep", "-lI", "--cached", "-e", key).stdout.strip()
check("추적 중인 파일에 키가 없다", not tracked, tracked or "clean")

history = git("log", "--all", "-S", key, "--oneline").stdout.strip()
check("git 히스토리에 키가 없다", not history, history.splitlines()[0] if history else "clean")

worktree = git("grep", "-lI", "-e", key, "--", ".").stdout.strip()
check("작업트리(추적 대상)에 키가 없다", not worktree, worktree or "clean")

# 3. .gitignore가 비밀 파일들을 실제로 덮는가
rel = PROJECT.relative_to(REPO)
for candidate in (".env", ".env.local", "sub/deep/.env", "secrets/x.txt", "a.key"):
    check(f".gitignore가 {candidate} 를 제외한다", is_ignored(str(rel / candidate)))
check(".env.example 은 커밋된다(제외되지 않는다)", not is_ignored(str(rel / ".env.example")))

# 4. 예제 파일에 진짜 값이 들어가 있지 않은가
example = PROJECT / ".env.example"
if example.is_file():
    value = config._parse_env_file(example).get(config.ENV_VAR, "")
    check(".env.example 에 실제 키가 없다", not value.startswith(config.KEY_PREFIX), value or "(빈 값)")
else:
    notes.append(".env.example 이 없습니다.")

# 5. 키 파일 권한
if config.SECRET_FILE.is_file():
    mode = config.SECRET_FILE.stat().st_mode & 0o777
    check("키 파일 권한이 600 이다", mode == 0o600, oct(mode)[2:])
    dmode = config.SECRET_FILE.parent.stat().st_mode & 0o777
    check("키 디렉터리 권한이 700 이다", dmode == 0o700, oct(dmode)[2:])

# 6. (선택) 키가 실제로 살아있는가 — OpenRouter 본인에게만 보낸다
if "--online" in sys.argv:
    import json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp).get("data", {})
        check("OpenRouter가 키를 인정한다", True, f"usage={data.get('usage')} limit={data.get('limit')}")
    except urllib.error.HTTPError as exc:
        check("OpenRouter가 키를 인정한다", False, f"HTTP {exc.code} — 키가 폐기·오타일 수 있습니다")
    except Exception as exc:  # 네트워크 차단 등
        notes.append(f"온라인 확인을 못 했습니다: {config.redact(exc)}")
else:
    notes.append("키가 살아있는지까지 보려면 --online 을 붙이세요.")

print()
for n in notes:
    print(f"참고: {n}")
if failures:
    print(f"\n실패 {len(failures)}건: " + ", ".join(failures), file=sys.stderr)
    raise SystemExit(1)
print("\n전부 통과.")
