"""실제 PDF → 실제 무료 모델 요약(품질·시간 평가용). 개발 전용(앱은 이 파일을 로드하지 않는다).

    python3 -B tools/live_summaries.py --out DIR FILE.pdf [FILE.pdf …]
    python3 -B tools/live_summaries.py --out DIR --chunk 60000 long.pdf     # 조각 크기를 바꿔 비교(이번 실행에만)
    python3 -B tools/live_summaries.py --out DIR --max-calls 6 …            # 이번 실행의 실제 호출 상한(기본 6)

**실제 모델을 부른다(무료 `:free`만, 하루 50회 한도에서 빠짐).** 요약 1건이 쓴 호출 수를 세어 찍고,
DIR/live_calls.jsonl에 누적한다. 하루 한도(DAILY_LIMIT)를 만나면 바로 멈춘다.
pdf_extract.extract → llm.summarize를 서버 없이 그대로 부른다(서버 오버헤드는 수 ms라 시간 비교에 영향 없음).
결과 전문(요약·원문 앞부분)은 DIR/live_<파일>.json에 저장한다 — DIR은 저장소 밖에 둔다.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import llm  # noqa: E402
import pdf_extract  # noqa: E402

assert Path(llm.__file__).resolve().parent == APP_DIR, llm.__file__


def calls_from_log(log: str) -> int:
    m = re.search(r"호출 (\d+)회", log or "")
    return int(m.group(1)) if m else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--chunk", type=int, default=0, help="llm.CHUNK_CHARS를 이번 실행에만 바꾼다")
    ap.add_argument("--max-calls", type=int, default=6, help="이번 실행 전체의 실제 호출 상한")
    ap.add_argument("files", nargs="+")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.chunk:
        llm.CHUNK_CHARS = a.chunk
        llm.MAX_CHUNKS = max(1, math.ceil(llm.MAX_INPUT_CHARS / a.chunk))
    for m in llm.MODELS:
        llm.assert_free(m)
    print(f"모델 {llm.MODELS} · CHUNK_CHARS {llm.CHUNK_CHARS} · MAX_CHUNKS {llm.MAX_CHUNKS}")
    used = 0
    for f in a.files:
        path = Path(f)
        if used >= a.max_calls:
            print(f"[멈춤] 호출 상한 {a.max_calls}회에 닿아 {path.name}부터 건너뜀")
            break
        info = pdf_extract.extract(path.read_bytes())
        meta = {"title": info["title"], "file_name": path.name, "pages": info["pages"],
                "pages_used": info["pages_used"], "truncated": info["truncated"]}
        t0 = time.monotonic()
        rec = {"file": path.name, "chars": info["chars"], "pages": info["pages"], "pages_used": info["pages_used"],
               "truncated": info["truncated"], "chunk_chars": llm.CHUNK_CHARS, "at": time.strftime("%H:%M:%S")}
        try:
            r = llm.summarize(info["text"], meta)
            rec.update(ok=True, calls=r["calls"], chunks=r["chunks"], model=r["model"], one_line=r["one_line"],
                       overview=r["overview"], points=r["points"])
        except llm.SummarizeError as e:
            rec.update(ok=False, code=e.code, calls=calls_from_log(e.log), log=e.log, reset_at=e.reset_at)
        rec["elapsed_s"] = round(time.monotonic() - t0, 1)
        used += rec["calls"]
        with open(out / "live_calls.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({k: rec[k] for k in ("at", "file", "chunk_chars", "calls", "ok", "elapsed_s")}, ensure_ascii=False) + "\n")
        (out / f"live_{path.stem}_{llm.CHUNK_CHARS}.json").write_text(
            json.dumps({**rec, "text_head": info["text"][:1500]}, ensure_ascii=False, indent=2), encoding="utf-8")
        status = "OK" if rec["ok"] else f"실패 {rec['code']}"
        print(f"\n== {path.name}  {info['pages']}쪽(사용 {info['pages_used']}) {info['chars']:,}자 → {status} · "
              f"호출 {rec['calls']}회 · 조각 {rec.get('chunks', '-')} · {rec['elapsed_s']}초 · {rec.get('model', '')}")
        if rec["ok"]:
            print("한 줄:", rec["one_line"])
            print("요약:", rec["overview"])
            for p in rec["points"]:
                print("  -", p)
        else:
            print("log:", rec["log"])
            if rec["code"] == "DAILY_LIMIT":
                print("[멈춤] 하루 한도 소진 — 실제 호출 시험을 멈춘다")
                break
    total = sum(json.loads(l)["calls"] for l in open(out / "live_calls.jsonl", encoding="utf-8"))
    print(f"\n이번 실행 실제 호출 {used}회 · {out / 'live_calls.jsonl'} 누적 {total}회")
    return 0


if __name__ == "__main__":
    sys.exit(main())
