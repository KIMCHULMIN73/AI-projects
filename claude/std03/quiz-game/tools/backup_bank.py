#!/usr/bin/env python3
"""문제 은행 전체를 타임스탬프 붙여 보관한다.

  python3 tools/backup_bank.py            # .backups/ 아래에 새 스냅숏
  python3 tools/backup_bank.py --list     # 보관된 스냅숏 목록
  python3 tools/backup_bank.py --verify <경로>   # 스냅숏이 지금 은행과 같은지

data/questions/ 네 파일과 진입점 questions.js를 통째로 담고, 문항 수·체크섬을
manifest.json에 함께 적는다. 복원은 파일을 되돌려 놓기만 하면 된다.

[개발용] 앱은 이 파일을 로드하지 않는다.
"""
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BACKUPS = ROOT / ".backups"
FILES = ["questions.js"] + [f"questions/{s}.js" for s in
                            ["korean-history", "science", "world-geography", "arts-culture"]]
KEEP = 30  # 최근 30개만 남긴다 — 매일 돌려도 한 달치


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def count(path):
    return path.read_text(encoding="utf-8").count("\n    id: '")


def manifest_now():
    return {rel: {"sha256": digest(DATA / rel), "questions": count(DATA / rel)}
            for rel in FILES}


def do_backup():
    BACKUPS.mkdir(exist_ok=True)
    # 백업 폴더는 git에 올리지 않는다. 폴더 스스로 그것을 선언하게 해 둔다.
    # .gitignore 자신까지 무시해야 폴더가 git status에 아예 뜨지 않는다
    # ("!.gitignore"로 예외를 두면 추적되지 않은 파일이 남아 폴더가 노출된다).
    gi = BACKUPS / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n", encoding="utf-8")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = BACKUPS / stamp
    if dest.exists():
        print(f"✗ 같은 이름의 스냅숏이 이미 있습니다: {dest}")
        return 1
    (dest / "questions").mkdir(parents=True)
    for rel in FILES:
        shutil.copy2(DATA / rel, dest / rel)

    man = manifest_now()
    total = sum(v["questions"] for k, v in man.items() if k != "questions.js")
    (dest / "manifest.json").write_text(
        json.dumps({"created": stamp, "total": total, "files": man},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✓ 백업 완료: .backups/{stamp}  ({total}문항, 파일 {len(FILES)}개)")
    for rel in FILES:
        if rel != "questions.js":
            print(f"    {rel}  {man[rel]['questions']:>4}문항  {man[rel]['sha256'][:12]}")

    old = sorted(p for p in BACKUPS.iterdir() if p.is_dir())
    for p in old[:-KEEP]:
        shutil.rmtree(p)
        print(f"    (오래된 스냅숏 삭제: {p.name})")
    return 0


def do_list():
    if not BACKUPS.exists() or not any(BACKUPS.iterdir()):
        print("보관된 스냅숏이 없습니다.")
        return 0
    for p in sorted(d for d in BACKUPS.iterdir() if d.is_dir()):
        try:
            m = json.loads((p / "manifest.json").read_text(encoding="utf-8"))
            print(f"  {p.name}  {m['total']}문항")
        except Exception:
            print(f"  {p.name}  (manifest 없음 — 손상되었을 수 있음)")
    return 0


def do_verify(target):
    p = Path(target)
    if not p.is_absolute():
        p = BACKUPS / target
    man_path = p / "manifest.json"
    if not man_path.exists():
        print(f"✗ manifest를 찾을 수 없습니다: {man_path}")
        return 1
    saved = json.loads(man_path.read_text(encoding="utf-8"))["files"]
    now = manifest_now()
    diff = [rel for rel in FILES if saved.get(rel, {}).get("sha256") != now[rel]["sha256"]]
    if diff:
        print(f"✗ 스냅숏과 지금 은행이 다릅니다 ({len(diff)}개 파일):")
        for rel in diff:
            was = saved.get(rel, {}).get("questions", "?")
            now_n = now[rel]["questions"]
            how = f"{was}문항 → {now_n}문항" if was != now_n else f"{now_n}문항 그대로, 내용이 다름"
            print(f"    {rel}  {how}")
        return 1
    print(f"✓ 스냅숏 {p.name}이(가) 지금 은행과 일치합니다.")
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "--list":
        sys.exit(do_list())
    if a and a[0] == "--verify":
        sys.exit(do_verify(a[1] if len(a) > 1 else ""))
    sys.exit(do_backup())
