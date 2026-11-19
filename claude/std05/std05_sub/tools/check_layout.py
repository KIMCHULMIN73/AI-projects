"""pdf_layout.py 모듈 단위 점검 (SPEC §10.11 "3단계 — backend"). 서버 없이 모듈을 직접 부른다. **모델 호출 0회.**

    python3 -B tools/check_layout.py --pdfs DIR           # DIR에 시험 PDF가 없으면 make_test_pdfs로 만든다
    python3 -B tools/check_layout.py --pdfs DIR --real    # + 실제 문서(real_*.pdf) 결과·시간 기록

- 기대 결과는 tools/make_test_pdfs.py의 LAYOUT_CASES["expect"]와 CASES(오류 코드)에서 가져온다.
- 상수를 낮춰(FIGURES_PER_PAGE·LAYOUT_FIGURE_BYTES·EXPORT_FIGURE_BYTES·예산) LIMIT·TIME 경로를 시험하고 글자가 빠지지 않는지 본다.
- 내려받을 HTML의 안전성(스크립트 0, 외부 URL 0, CSP, 이스케이프, 목차 링크)을 브라우저 없이 확인한다.
- 시험 전후 임시 폴더(/tmp의 pdf-layout-*)와 앱 폴더에 새 파일이 남지 않는지 본다.
- 종료코드: 모두 통과 0, 실패 있으면 1.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))
sys.path.insert(1, str(TOOLS_DIR))
import pdf_extract  # noqa: E402
import pdf_layout as L  # noqa: E402
import make_test_pdfs as mtp  # noqa: E402

assert Path(L.__file__).resolve().parent == APP_DIR, "상위 폴더의 같은 이름 모듈을 불러왔다"

DATA_URI_RE = re.compile(r"\Adata:image/(?:png|jpeg);base64,[A-Za-z0-9+/]+={0,2}\Z")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
CONSTS = ("FIGURES_PER_PAGE", "LAYOUT_FIGURE_BYTES", "EXPORT_FIGURE_BYTES", "LAYOUT_BUDGET_S", "EXPORT_BUDGET_S",
          "LAYOUT_PAGES_PER_REQ")
DEFAULTS = {k: getattr(L, k) for k in CONSTS}


class Checker:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[str] = []

    def ok(self, cond: bool, label: str, detail: str = "") -> bool:
        if cond:
            self.passed += 1
            print(f"  ok   {label}")
        else:
            self.failed.append(label)
            print(f"  FAIL {label}  {detail}")
        return cond


def restore() -> None:
    for k, v in DEFAULTS.items():
        setattr(L, k, v)


# ── §10.4 형식 검사 ────────────────────────────────────────────────────

def _strs(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strs(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strs(v)


def schema_problems(r: dict) -> list[str]:
    """SPEC §10.4 규칙 위반 목록(빈 목록이면 통과)."""
    bad: list[str] = []
    try:
        json.dumps(r, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        return [f"JSON 직렬화 실패 {type(exc).__name__}"]
    if set(r) != {"doc", "from", "to", "body_size", "pages", "notes"}:
        bad.append(f"최상위 키 {sorted(r)}")
    doc = r.get("doc", {})
    if set(doc) != {"pages", "title", "page_sizes", "outline", "outline_source"}:
        bad.append(f"doc 키 {sorted(doc)}")
    n = doc.get("pages", 0)
    if len(doc.get("page_sizes", [])) != n:
        bad.append("page_sizes 길이 ≠ pages")
    if doc.get("outline_source") != ("bookmarks" if doc.get("outline") else "none"):
        bad.append("outline_source")
    for it in doc.get("outline", []):
        if set(it) != {"title", "page", "level"} or not 1 <= it["page"] <= n or not 1 <= it["level"] <= 6 \
                or not it["title"] or len(it["title"]) > 200:
            bad.append(f"outline 항목 {it}")
            break
    if not (1 <= r["from"] <= r["to"] <= n) or r["to"] - r["from"] + 1 > L.LAYOUT_PAGES_PER_REQ:
        bad.append(f"from/to {r['from']}~{r['to']}")
    if [p["number"] for p in r["pages"]] != list(range(r["from"], r["to"] + 1)):
        bad.append("pages[].number가 from~to와 다름")
    if not isinstance(r["body_size"], (int, float)) or not 4 <= r["body_size"] <= 72:
        bad.append(f"body_size {r['body_size']}")
    ids = []
    figs = omitted = 0
    for p in r["pages"]:
        if set(p) != {"number", "width", "height", "figures_failed", "background_skipped", "blocks"}:
            bad.append(f"page 키 {sorted(p)}")
        W, H = p["width"], p["height"]
        for b in p["blocks"]:
            ids.append(b.get("id"))
            if not re.fullmatch(rf"p{p['number']}-b\d+", str(b.get("id"))):
                bad.append(f"id 형식 {b.get('id')}")
            for k in ("x", "y", "w", "h"):
                if not isinstance(b.get(k), (int, float)) or round(b[k], 2) != b[k]:
                    bad.append(f"{b['id']} {k} 형식")
            if b["x"] < -1 or b["y"] < -1 or b["x"] + b["w"] > W + 1 or b["y"] + b["h"] > H + 1 or b["w"] < 0 or b["h"] < 0:
                bad.append(f"{b['id']} 좌표가 쪽 밖 {b['x'], b['y'], b['w'], b['h']} / {W}x{H}")
            t = b["type"]
            if t == "text":
                if set(b) != {"id", "type", "role", "x", "y", "w", "h", "text", "lines"} or b["role"] not in ("h1", "h2", "h3", "p"):
                    bad.append(f"{b['id']} text 키·role")
                for ln in b["lines"]:
                    if set(ln) != {"x", "y", "w", "h", "size", "bold", "text"} or not isinstance(ln["bold"], bool):
                        bad.append(f"{b['id']} line 키")
                        break
            elif t == "table":
                rows = b.get("rows", [])
                if set(b) != {"id", "type", "x", "y", "w", "h", "size", "cols", "rows"}:
                    bad.append(f"{b['id']} table 키")
                if len(rows) < 2 or len(rows[0]) < 2 or any(len(row) != len(rows[0]) for row in rows) \
                        or len(b["cols"]) != len(rows[0]) or b["cols"] != sorted(b["cols"]):
                    bad.append(f"{b['id']} rows·cols 모양")
                if any(set(c) != {"text", "bold"} for row in rows for c in row):
                    bad.append(f"{b['id']} 셀 키")
            elif t == "figure":
                figs += 1
                if set(b) != {"id", "type", "x", "y", "w", "h", "src", "text", "omitted"}:
                    bad.append(f"{b['id']} figure 키")
                if b["omitted"] not in (None, "LIMIT", "TIME", "FAILED"):
                    bad.append(f"{b['id']} omitted {b['omitted']}")
                if b["omitted"] is None and not (isinstance(b["src"], str) and DATA_URI_RE.match(b["src"])):
                    bad.append(f"{b['id']} src 형식")
                if b["omitted"] is not None:
                    omitted += 1
                    if b["src"] is not None:
                        bad.append(f"{b['id']} 생략인데 src 있음")
            else:
                bad.append(f"모르는 type {t}")
    if len(ids) != len(set(ids)):
        bad.append("id 중복")
    if r["notes"] != {"figures": figs, "figures_omitted": omitted}:
        bad.append(f"notes {r['notes']} ≠ 실제 {figs}/{omitted}")
    for s in _strs(r):
        if CONTROL_RE.search(s) or any(0xD800 <= ord(ch) <= 0xDFFF for ch in s):
            bad.append("제어문자·서로게이트가 든 문자열")
            break
    return bad[:8]


def load_all(data: bytes, step: int = 5) -> list[dict]:
    """보기 창처럼 5쪽씩 끝까지(두 번째부터 body를 넘김)."""
    first = L.layout(data, 1, step)
    out = [first]
    n = first["doc"]["pages"]
    f = step + 1
    while f <= n:
        out.append(L.layout(data, f, f + step - 1, first["body_size"]))
        f += step
    return out


def blocks_of(results: list[dict]) -> list[dict]:
    return [b for r in results for p in r["pages"] for b in p["blocks"]]


def all_text(blocks: list[dict]) -> str:
    parts = []
    for b in blocks:
        if b["type"] == "text":
            parts.append(b["text"])
        elif b["type"] == "table":
            parts.extend(c["text"] for row in b["rows"] for c in row)
        else:
            parts.append(b["text"])
    return "\n".join(parts)


# ── HTML 안전성 ────────────────────────────────────────────────────────

def html_problems(text: str, stats: dict) -> list[str]:
    bad = []
    if re.search(r"<script", text, re.I):
        bad.append("<script 있음")
    if not text.startswith("<!doctype html>\n") or "\r" in text or text.startswith("﻿"):
        bad.append("머리(doctype·줄 끝·BOM)")
    csp = "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src data:; style-src 'unsafe-inline'\">"
    if csp not in text:
        bad.append("CSP 메타 없음")
    no_data = re.sub(r"data:image/[a-z]+;base64,[A-Za-z0-9+/=]+", "", text)
    if any(re.search(r"\son[a-z]+\s*=", tag, re.I) for tag in re.findall(r"<[^>]*>", no_data)):
        bad.append("태그에 on* 속성")
    attrs = re.findall(r'\s(href|src|action|srcset|poster|data)="([^"]*)"', text)
    for name, val in attrs:
        if name == "href" and not val.startswith("#"):
            bad.append(f"href 외부 {val[:40]}")
        elif name == "src" and not DATA_URI_RE.match(val):
            bad.append(f"src 형식 {val[:40]}")
        elif name not in ("href", "src"):
            bad.append(f"속성 {name}")
    stripped = re.sub(r'"data:image/[a-z]+;base64,[A-Za-z0-9+/=]+"', '""', text)
    tags_only = " ".join(re.findall(r"<[^>]*>", stripped)) + " ".join(re.findall(r"<style>(.*?)</style>", stripped, re.S))
    for needle in ("http:", "https:", "//", "url(", "@import"):
        if needle in tags_only:
            bad.append(f"태그·스타일에 {needle}")
    ids = set(re.findall(r'\sid="([^"]+)"', text))
    links = re.findall(r'href="#([^"]+)"', text)
    missing = [h for h in links if h not in ids]
    if missing:
        bad.append(f"목차 링크 대상 없음 {missing[:3]}")
    n = stats["figures_omitted"]
    notice = f"그림 {n}개는 파일 크기·시간 제한으로 넣지 않았습니다."
    if (n > 0) != (notice in text) or (n == 0 and "넣지 않았습니다" in text):
        bad.append("생략 안내 문구와 생략 수 불일치")
    marks = text.count('class="omitted" id=')
    if marks != n:
        bad.append(f"생략 자리 표시 {marks} ≠ {n}")
    if text.count("<figure ") + n != stats["figures"]:
        bad.append("그림 수 불일치")
    return bad


# ── 점검 묶음 ──────────────────────────────────────────────────────────

def check_layout_cases(c: Checker, pdfs: Path, timings: dict) -> None:
    print("\n[보기] LAYOUT_CASES 기대 결과 (SPEC §10.11)")
    for name, case in mtp.LAYOUT_CASES.items():
        data = (pdfs / name).read_bytes()
        exp = case["expect"]
        t0 = time.monotonic()
        res = load_all(data)
        timings[f"{name} 전체(5쪽씩)"] = time.monotonic() - t0
        probs = [p for r in res for p in schema_problems(r)]
        c.ok(not probs, f"{name} §10.4 형식", str(probs[:4]))
        doc = res[0]["doc"]
        blocks = blocks_of(res)
        tables = [b for b in blocks if b["type"] == "table"]
        figs = [b for b in blocks if b["type"] == "figure"]
        if "pages" in exp:
            c.ok(doc["pages"] == exp["pages"], f"{name} pages={exp['pages']}", str(doc["pages"]))
        if "outline" in exp:
            c.ok(len(doc["outline"]) == exp["outline"] and doc["outline_source"] == exp["outline_source"],
                 f"{name} 책갈피 {exp['outline']}항목·{exp['outline_source']}",
                 f"{len(doc['outline'])} {doc['outline_source']}")
        if "tables" in exp:
            c.ok(len(tables) == exp["tables"], f"{name} 표 {exp['tables']}개", str(len(tables)))
        if "table" in exp and tables:
            got = [[cell["text"] for cell in row] for row in tables[0]["rows"]]
            c.ok(got == exp["table"], f"{name} 표 셀 글자 일치", str(got))
            c.ok(all(cell["bold"] for cell in tables[0]["rows"][0])
                 and not any(cell["bold"] for row in tables[0]["rows"][1:] for cell in row),
                 f"{name} 표 머리글만 굵게")
        if "figures" in exp:
            c.ok(len(figs) == exp["figures"], f"{name} 그림 {exp['figures']}개", str(len(figs)))
        if "figure_pt" in exp and figs:
            w, h = exp["figure_pt"]
            c.ok(abs(figs[0]["w"] - w) <= 3 and abs(figs[0]["h"] - h) <= 3 and figs[0]["src"],
                 f"{name} 그림 약 {w}×{h}pt·src 있음", f"{figs[0]['w']}×{figs[0]['h']}")
        if "roles" in exp:
            roles = {b.get("role") for b in res[0]["pages"][0]["blocks"] if b["type"] == "text"}
            c.ok(set(exp["roles"]) <= roles, f"{name} 1쪽에 {exp['roles']}", str(sorted(roles)))
        if "text_has" in exp:
            txt = all_text(blocks)
            miss = [s for s in exp["text_has"] if s not in txt]
            c.ok(not miss, f"{name} 글자 그대로(띄어쓰기 유지)", str(miss))
        if "order" in exp:
            txt = all_text(blocks)
            pos = [txt.find(s) for s in exp["order"]]
            c.ok(all(p >= 0 for p in pos) and pos == sorted(pos), f"{name} 읽는 순서(왼쪽 단 먼저)", str(pos))
        if "page_size" in exp:
            w, h = exp["page_size"]
            got = doc["page_sizes"][0]
            c.ok(abs(got[0] - w) <= 2 and abs(got[1] - h) <= 2
                 and abs(res[0]["pages"][0]["width"] - w) <= 2, f"{name} 쪽 크기 약 {w}×{h}", str(got))
        if "limit_from" in exp:
            p1 = [b for b in res[0]["pages"][0]["blocks"] if b["type"] == "figure"]
            k = exp["limit_from"] - 1
            c.ok(len(p1) > k and all(b["omitted"] is None for b in p1[:k])
                 and all(b["omitted"] == "LIMIT" for b in p1[k:]),
                 f"{name} 1쪽 {exp['limit_from']}번째 그림부터 LIMIT", str([b["omitted"] for b in p1][k - 1:k + 2]))
            jpeg = [b for b in figs if (b["src"] or "").startswith("data:image/jpeg")]
            c.ok(len(jpeg) >= 1, f"{name} 큰 사진 그림은 JPEG로(PNG {L.FIGURE_PNG_MAX_BYTES // 1024}KB 초과)")
        if name == "struct.pdf":
            # 같은 문서·같은 body면 요청 범위가 달라도 id가 같다
            a = L.layout(data, 1, 2)
            b2 = L.layout(data, 2, 2, a["body_size"])
            c.ok([x["id"] for x in a["pages"][1]["blocks"]] == [x["id"] for x in b2["pages"][0]["blocks"]],
                 "struct.pdf id가 요청 범위와 무관")
            heads = [b for b in a["pages"][0]["blocks"] if b["type"] == "text"]
            c.ok(heads[0]["role"] == "h1" and heads[0]["lines"][0]["size"] == 24.0 and heads[0]["lines"][0]["bold"],
                 "struct.pdf 첫 제목 크기 24pt·굵게(pdftohtml 꾸밈 맞춤)", str(heads[0]["lines"][0]))
            fig = [b for b in a["pages"][0]["blocks"] if b["type"] == "figure"][0]
            before = [b.get("text", "") for b in a["pages"][0]["blocks"]]
            idx = a["pages"][0]["blocks"].index(fig)
            c.ok(before[idx - 1] == "그림 1" and before[idx + 1].startswith("그림은"),
                 "struct.pdf 그림이 제목 '그림 1'과 다음 문단 사이")
            png = base64.b64decode(fig["src"].split(",", 1)[1])
            c.ok(png[:8] == b"\x89PNG\r\n\x1a\n" and 230 <= int.from_bytes(png[16:20], "big") <= 250,
                 "struct.pdf 그림 픽셀 = 96dpi PNG(폭 약 240px)", str(int.from_bytes(png[16:20], "big")))
        if name == "xss.pdf":
            c.ok(doc["title"] == mtp.XSS_TITLE, "xss.pdf 제목이 글자 그대로", doc["title"])


def check_existing_cases(c: Checker, pdfs: Path, timings: dict) -> None:
    print("\n[보기] 기존 시험 PDF — 표 오탐·스캔 쪽·오류 코드")
    for name in ("ko.pdf", "en.pdf", "multi.pdf", "long.pdf", "mid.pdf", "inject.pdf", "restricted.pdf", "tiny.pdf"):
        data = (pdfs / name).read_bytes()
        t0 = time.monotonic()
        res = load_all(data)
        timings[f"{name} 전체(5쪽씩)"] = time.monotonic() - t0
        blocks = blocks_of(res)
        probs = [p for r in res for p in schema_problems(r)]
        c.ok(not probs and not [b for b in blocks if b["type"] == "table"],
             f"{name} 형식 통과·표 0개(오탐 없음)", str(probs[:3]))
    long_pages = load_all((pdfs / "long.pdf").read_bytes())
    ex = pdf_extract.extract((pdfs / "long.pdf").read_bytes())
    c.ok(sum(len(r["pages"]) for r in long_pages) == ex["pages"], "long.pdf 잘림 없이 문서 전체 쪽")
    for name in ("mixed.pdf", "scan.pdf"):
        res = L.layout((pdfs / name).read_bytes())
        last = res["pages"][-1]
        figs = [b for b in last["blocks"] if b["type"] == "figure"]
        c.ok(len(figs) == 1 and figs[0]["src"] and figs[0]["w"] > 600 and last["background_skipped"] == 0
             and not last["figures_failed"],
             f"{name} 스캔한 쪽 = 전면 그림 하나(background_skipped 0)",
             f"figs={[(b['w'], b['h'], b['omitted']) for b in figs]} failed={last['figures_failed']}")
    c.ok(True, "scan.pdf는 NO_TEXT가 아니라 200(글자 없어도 쪽·그림을 보여 줌)")
    errors = {"encrypted.pdf": "ENCRYPTED", "broken.pdf": "BROKEN_PDF", "fake.pdf": "NOT_PDF", "zero.pdf": "EMPTY_FILE",
              "pages301.pdf": "TOO_LONG", "fake_header.pdf": "BROKEN_PDF"}
    for name, code in errors.items():
        data = (pdfs / name).read_bytes()
        for fn, label in ((lambda d: L.layout(d), "layout"), (lambda d: L.export_html(d, name), "export_html")):
            try:
                fn(data)
                got = "성공"
            except pdf_extract.ExtractError as exc:
                got = exc.code
            except Exception as exc:  # noqa: BLE001
                got = f"다른 예외 {type(exc).__name__}"
            c.ok(got == code, f"{label}({name}) → {code}", got)


def check_ranges(c: Checker, pdfs: Path) -> None:
    print("\n[보기] from·to 정리와 BAD_RANGE (SPEC §10.3)")
    multi = (pdfs / "multi.pdf").read_bytes()       # 5쪽
    longd = (pdfs / "long.pdf").read_bytes()        # 78쪽
    r = L.layout(longd)
    c.ok((r["from"], r["to"]) == (1, 10), "범위 없음 → 1~10", f"{r['from']}~{r['to']}")
    r = L.layout(longd, 3)
    c.ok((r["from"], r["to"]) == (3, 12), "from만 → from~from+9")
    r = L.layout(longd, 5, 40)
    c.ok((r["from"], r["to"]) == (5, 14), "10쪽 넘는 범위 → from+9로 줄임")
    r = L.layout(multi, 4, 99)
    c.ok((r["from"], r["to"]) == (4, 5), "to > 쪽수 → 쪽수로")
    r = L.layout(multi, 5, 5)
    c.ok((r["from"], r["to"]) == (5, 5) and len(r["pages"]) == 1, "한 쪽만")
    for args, label in (((0, None), "from=0"), ((6, None), "from > 쪽수"), ((3, 2), "to < from"),
                        (("1", None), "from 문자열"), ((1.5, None), "from 실수"), ((True, None), "from bool"),
                        ((-1, None), "from 음수")):
        try:
            L.layout(multi, *args)
            got = "성공"
        except pdf_extract.ExtractError as exc:
            got = exc.code
        c.ok(got == "BAD_RANGE", f"{label} → BAD_RANGE", got)
    r = L.layout(multi, 1, 2, 9.5)
    c.ok(r["body_size"] == 9.5, "body 쿼리를 주면 그대로 body_size")
    r = L.layout(multi, 1, 2, 100)
    c.ok(r["body_size"] == 12.0, "body 범위 밖 → 문서 전체로 계산(12pt)", str(r["body_size"]))


def check_limits(c: Checker, pdfs: Path) -> None:
    print("\n[보기] 상수를 낮춰 LIMIT·TIME 경로 — 글자는 빠지지 않음")
    struct = (pdfs / "struct.pdf").read_bytes()
    figs = (pdfs / "figs.pdf").read_bytes()
    base_text = all_text(blocks_of([L.layout(struct)]))
    try:
        L.FIGURES_PER_PAGE = 1
        r = L.layout(figs, 1, 1)
        om = [b["omitted"] for b in r["pages"][0]["blocks"] if b["type"] == "figure"]
        c.ok(om[0] is None and set(om[1:]) == {"LIMIT"} and r["notes"]["figures_omitted"] == len(om) - 1,
             "FIGURES_PER_PAGE=1 → 2번째부터 LIMIT", str(om[:4]))
        restore()
        L.LAYOUT_FIGURE_BYTES = 10
        r = L.layout(struct)
        fig = [b for b in r["pages"][0]["blocks"] if b["type"] == "figure"][0]
        c.ok(fig["omitted"] == "LIMIT" and fig["src"] is None and all_text(blocks_of([r])) == base_text,
             "LAYOUT_FIGURE_BYTES=10 → LIMIT, 글자 그대로")
        restore()
        r = L.layout(struct, deadline=time.monotonic() - 1)
        fig = [b for b in r["pages"][0]["blocks"] if b["type"] == "figure"]
        c.ok(len(fig) == 1 and fig[0]["omitted"] == "TIME" and all_text(blocks_of([r])) == base_text,
             "예산 지남 → 그림 TIME, 글자 그대로", str([b["omitted"] for b in fig]))
        L.LAYOUT_BUDGET_S = 0
        r = L.layout(struct)
        c.ok([b["omitted"] for b in r["pages"][0]["blocks"] if b["type"] == "figure"] == ["TIME"],
             "LAYOUT_BUDGET_S=0 → TIME")
        restore()
        L.EXPORT_FIGURE_BYTES = 10
        text, stats = L.export_html(figs, "figs.pdf")
        c.ok(stats["figures"] == 42 and stats["figures_omitted"] == 42 and not html_problems(text, stats)
             and "그림 42개는 파일 크기·시간 제한으로 넣지 않았습니다." in text and "<img " not in text,
             "EXPORT_FIGURE_BYTES=10 → 모두 자리 표시 + 안내 42개", str(stats) + str(html_problems(text, stats)))
        restore()
        L.EXPORT_BUDGET_S = 0
        text, stats = L.export_html(struct, "struct.pdf")
        c.ok(stats["figures_omitted"] == 1 and "[그림 생략: 시간 제한]" in text and mtp.STRUCT_P2 in text,
             "EXPORT_BUDGET_S=0 → 시간 제한 자리 표시, 글자는 끝까지")
    finally:
        restore()


def check_export(c: Checker, pdfs: Path, timings: dict) -> None:
    print("\n[내려받기] export_html 안전성·내용 (SPEC §10.6)")
    for name in list(mtp.LAYOUT_CASES) + ["mixed.pdf", "long.pdf", "ko.pdf"]:
        data = (pdfs / name).read_bytes()
        t0 = time.monotonic()
        text, stats = L.export_html(data, name)
        timings[f"{name} 내려받기"] = time.monotonic() - t0
        timings[f"{name} 내려받기 크기KB"] = len(text.encode()) / 1024
        probs = html_problems(text, stats)
        pages = len(load_all(data)[0]["doc"]["page_sizes"])
        c.ok(not probs and stats["pages"] == pages and text.count('<section class="page"') == pages,
             f"{name} HTML 안전성·모든 쪽 section", str(probs))
    text, stats = L.export_html((pdfs / "struct.pdf").read_bytes(), "struct.pdf")
    c.ok(text.count("<table ") == 1 and text.count("<figure ") == 1 and text.count("<th>") == 3
         and '<li><a href="#p1">1.1 현황</a>' in text and "<nav aria-label=\"목차\">" in text,
         "struct.pdf: 표 1·그림 1·th 3·책갈피 목차(중첩)")
    c.ok('<h2 id="p1-b0">1. 도시 텃밭 보고서</h2>' in text and "<h3 " in text and "<h4 " in text,
         "struct.pdf: 제목 h1→h2, h2→h3, h3→h4")
    c.ok(text.count('<section class="page"') == 2 and 'id="p2"' in text, "struct.pdf: 쪽 section 2개")
    c.ok("<title>구조 시험 문서 — 추출한 텍스트</title>" in text and "원본: struct.pdf · 2쪽 ·" in text, "제목·원본 줄")
    text, stats = L.export_html((pdfs / "struct_noout.pdf").read_bytes(), "struct_noout.pdf")
    c.ok('href="#p1-b0"' in text and 'href="#p2-b0"' in text and not html_problems(text, stats),
         "책갈피 없음 → h1·h2 블록으로 목차(#p쪽-b번호)")
    text, stats = L.export_html((pdfs / "ko.pdf").read_bytes(), "ko.pdf")
    c.ok("<nav" in text, "ko.pdf: h1이 있어 목차가 생김")
    text, stats = L.export_html((pdfs / "long.pdf").read_bytes(), "long.pdf")
    c.ok("<nav" not in text, "long.pdf: 목차 항목이 없으면 nav 생략")

    print("\n[내려받기] xss.pdf")
    text, stats = L.export_html((pdfs / "xss.pdf").read_bytes(), '<img src=x onerror=alert(1)>".pdf')
    c.ok("<script" not in text.lower() and "<img src=x" not in text and "onerror=alert" in text,
         "xss.pdf: 스크립트·img 태그 없음(글자로만)")
    c.ok("&lt;script&gt;alert(1)&lt;/script&gt;" in text and "&lt;img src=x onerror=alert(1)&gt;" in text
         and "&quot;&#x27;&amp;" in text, "xss.pdf: 꺾쇠·따옴표·& 이스케이프")
    c.ok("<title>&lt;script&gt;alert(1)&lt;/script&gt; 제목 &quot;&#x27;&amp; — 추출한 텍스트</title>" in text,
         "xss.pdf: 제목 이스케이프")
    c.ok("&lt;/style&gt;&lt;/head&gt;" in text and text.count("</style>") == 1 and text.count("</head>") == 1,
         "xss.pdf: </style></head> 흉내가 문서 구조를 깨지 않음")
    c.ok("원본: &lt;img src=x onerror=alert(1)&gt;&quot;.pdf" in text, "파일 이름 이스케이프")
    c.ok(not html_problems(text, stats), "xss.pdf 공통 안전성", str(html_problems(text, stats)))
    # 이스케이프가 뚫렸다고 가정한 문자열도 블록 HTML에서 무력화되는지(직접 블록)
    blk = {"id": "p1-b0", "type": "figure", "x": 0, "y": 0, "w": 10, "h": 10,
           "src": "javascript:alert(1)", "text": "<b>x</b>", "omitted": None}
    out = L._block_html(blk, 1, 1)
    c.ok("javascript:" not in out and "<b>" not in out and "&lt;b&gt;" in out, "형식 틀린 src는 그리지 않음")


def check_file_names(c: Checker) -> None:
    print("\n[내려받기] 파일 이름 규칙 (SPEC §10.6)")
    cases = {
        "한글 이름 파일.pdf": "한글 이름 파일_추출.html",
        "보고서.PDF": "보고서_추출.html",
        'a\\b/c:d*e?f"g<h>i|j.pdf': "a_b_c_d_e_f_g_h_i_j_추출.html",
        "제어\x01문자.pdf": "제어_문자_추출.html",
        "  .점.  .pdf": "점_추출.html",
        "": "추출한_텍스트.html",
        ".pdf": "추출한_텍스트.html",
        "...": "추출한_텍스트.html",
        "가" * 150 + ".pdf": "가" * 100 + "_추출.html",
        "report.pdf.pdf": "report.pdf_추출.html",
        "notes.txt": "notes.txt_추출.html",
    }
    for src, want in cases.items():
        got = L.export_file_name(src)
        c.ok(got == want, f"{src[:30]!r} → {want[:30]}", got)


def check_tools_missing(c: Checker, pdfs: Path) -> None:
    print("\n[보기] 도구 없음")
    ko = (pdfs / "ko.pdf").read_bytes()
    old = os.environ.get("PATH", "")
    nobin = Path(tempfile.mkdtemp(prefix="check-layout-nobin-"))
    try:
        for tool in ("pdfinfo", "pdftotext", "pdftoppm"):
            os.symlink(f"/usr/bin/{tool}", nobin / tool)
        os.environ["PATH"] = str(nobin)
        t = L.check_tools()
        c.ok(t == {"pdftotext": True, "pdfinfo": True, "pdftohtml": False, "pdftoppm": True}, "check_tools 네 도구", str(t))
        for fn, label in ((lambda: L.layout(ko), "layout"), (lambda: L.export_html(ko), "export_html")):
            try:
                fn()
                got = "성공"
            except pdf_extract.ExtractError as exc:
                got = exc.code
            c.ok(got == "TOOL_MISSING", f"pdftohtml 없음 → {label} TOOL_MISSING", got)
        try:
            L.layout(b"plain")
            got = "성공"
        except pdf_extract.ExtractError as exc:
            got = exc.code
        c.ok(got == "NOT_PDF", "도구 없어도 NOT_PDF가 먼저")
    finally:
        os.environ["PATH"] = old
        for p in nobin.iterdir():
            p.unlink()
        nobin.rmdir()
    old_t = pdf_extract.EXTRACT_TIMEOUT_S
    try:
        pdf_extract.EXTRACT_TIMEOUT_S = 0.001
        try:
            L.layout((pdfs / "long.pdf").read_bytes())
            got = "성공"
        except pdf_extract.ExtractError as exc:
            got = exc.code
        c.ok(got == "EXTRACT_TIMEOUT", "도구 제한시간 0.001초 → EXTRACT_TIMEOUT", got)
    finally:
        pdf_extract.EXTRACT_TIMEOUT_S = old_t


def check_real(c: Checker, pdfs: Path, timings: dict, report: dict) -> None:
    print("\n[실제 문서] (--real)")
    mime = pdfs / "real_mime_en.pdf"
    if mime.is_file():
        data = mime.read_bytes()
        t0 = time.monotonic()
        res = load_all(data)
        timings["real_mime_en 전체(5쪽씩)"] = time.monotonic() - t0
        doc = res[0]["doc"]
        c.ok(len(doc["outline"]) == 24 and doc["outline_source"] == "bookmarks", "real_mime_en 책갈피 24항목",
             str(len(doc["outline"])))
        blocks = blocks_of(res)
        c.ok(not [p for r in res for p in schema_problems(r)], "real_mime_en 형식")
        tables = [b for b in blocks if b["type"] == "table"]
        report["real_mime_en"] = {
            "pages": doc["pages"], "body_size": res[0]["body_size"], "tables": len(tables),
            "table_shapes": [f"p{b['id'].split('-')[0][1:]} {len(b['rows'])}x{len(b['rows'][0])}" for b in tables],
            "table_first_rows": [[c2["text"] for c2 in b["rows"][0]] for b in tables[:4]],
            "headings": {r: sum(1 for b in blocks if b.get("role") == r) for r in ("h1", "h2", "h3")},
            "figures": sum(1 for b in blocks if b["type"] == "figure"),
        }
        t0 = time.monotonic()
        text, stats = L.export_html(data, mime.name)
        timings["real_mime_en 내려받기"] = time.monotonic() - t0
        c.ok(not html_problems(text, stats), "real_mime_en 내려받기 안전성", str(html_problems(text, stats)))
    guide = pdfs / "real_rpi_guide.pdf"
    if guide.is_file():
        data = guide.read_bytes()
        rows = []
        failed_pages = []
        figs_total = tables_total = 0
        heads = {"h1": 0, "h2": 0, "h3": 0}
        for first in (1, 11, 21, 31):
            t0 = time.monotonic()
            r = L.layout(data, first, first + 9)
            dt = time.monotonic() - t0
            probs = schema_problems(r)
            c.ok(not probs, f"real_rpi_guide {first}~{first + 9}쪽 형식", str(probs[:3]))
            figs = [b for p in r["pages"] for b in p["blocks"] if b["type"] == "figure"]
            figs_total += len(figs)
            tables_total += sum(1 for p in r["pages"] for b in p["blocks"] if b["type"] == "table")
            for p in r["pages"]:
                for b in p["blocks"]:
                    if b.get("role") in heads:
                        heads[b["role"]] += 1
            failed_pages += [p["number"] for p in r["pages"] if p["figures_failed"]]
            rows.append(f"{first}~{first + 9}쪽 {dt:.1f}s 그림 {len(figs)}(생략 {r['notes']['figures_omitted']}) "
                        f"배경 제외 {sum(p['background_skipped'] for p in r['pages'])} "
                        f"JSON {len(json.dumps(r)) // 1024}KB")
            timings[f"real_rpi_guide {first}~{first + 9}쪽"] = dt
        report["real_rpi_guide"] = {"pages": r["doc"]["pages"], "outline": len(r["doc"]["outline"]),
                                    "per_request": rows, "figures_1_40": figs_total, "tables_1_40": tables_total,
                                    "headings_1_40": heads, "figures_failed_pages": failed_pages}
        c.ok(r["doc"]["outline_source"] == "bookmarks" and figs_total > 0,
             "real_rpi_guide 책갈피·그림이 잡힘", f"outline {len(r['doc']['outline'])} figs {figs_total}")
    for name in ("real_xz_ko.pdf", "real_rpi_text.pdf"):
        p = pdfs / name
        if p.is_file():
            data = p.read_bytes()
            t0 = time.monotonic()
            res = load_all(data)
            timings[f"{name} 전체(5쪽씩)"] = time.monotonic() - t0
            blocks = blocks_of(res)
            report[name] = {"pages": res[0]["doc"]["pages"], "tables": sum(1 for b in blocks if b["type"] == "table"),
                            "headings": {r2: sum(1 for b in blocks if b.get("role") == r2) for r2 in ("h1", "h2", "h3")}}
            c.ok(not [x for r in res for x in schema_problems(r)], f"{name} 형식")


def snapshot(dirs: list[Path]) -> set[str]:
    seen = set()
    for d in dirs:
        for root, _subdirs, files in os.walk(d, onerror=lambda e: None):
            for f in files:
                seen.add(os.path.join(root, f))
            for s in _subdirs:
                seen.add(os.path.join(root, s) + "/")
    return seen


def _layout_chars(data: bytes) -> tuple:
    """(원문 bbox 단어의 글자 모음, layout 결과(문단·표 셀·그림 글자)의 글자 모음) — 공백 제외."""
    from collections import Counter
    raw = Counter()
    for bp in L._parse_bbox(L._run_ok("pdftotext", ["-enc", "UTF-8", "-bbox-layout", "-", "-"], data)):
        for b in bp.blocks:
            for ln in b:
                for w in ln.words:
                    raw.update(ch for ch in w.text if not ch.isspace())
    got = Counter()
    n = L.layout(data, 1, 1)["doc"]["pages"]
    for f in range(1, n + 1, L.LAYOUT_PAGES_PER_REQ):
        for p in L.layout(data, f, None, 12.0)["pages"]:
            for b in p["blocks"]:
                texts = [c["text"] for row in b["rows"] for c in row] if b["type"] == "table" else [b.get("text") or ""]
                for t in texts:
                    got.update(ch for ch in t if not ch.isspace())
    return raw, got


def check_stop_and_tables(c: Checker, pdfs: Path, real: bool) -> None:
    """(4단계 QA) 요청자가 떠났을 때 멈춤, 표 판정 개선(여러 줄 셀·그림 설명 쌍·빈 칸 많은 표)과 글자 보존."""
    print("\n[4단계: 멈춤·표 판정·글자 보존]")
    data = (pdfs / "struct.pdf").read_bytes()
    for name, fn in (("layout", lambda: L.layout(data, 1, None, None, None, should_stop=lambda: True)),
                     ("export_html", lambda: L.export_html(data, "s.pdf", should_stop=lambda: True))):
        try:
            fn()
            code = None
        except pdf_extract.ExtractError as exc:
            code = exc.code
        c.ok(code == "CLIENT_GONE", f"{name}(should_stop=참) → CLIENT_GONE", str(code))
    c.ok(L.layout(data, 1, None, should_stop=lambda: False)["pages"], "layout(should_stop=거짓) → 정상")
    names = ["struct.pdf", "xss.pdf", "twocol.pdf", "figs.pdf", "long.pdf"]
    if real:
        names += [n for n in ("real_mime_en.pdf", "real_xz_ko.pdf", "real_mkvmerge_ko.pdf", "real_rpi_text.pdf")
                  if (pdfs / n).is_file()]
    for name in names:
        raw, got = _layout_chars((pdfs / name).read_bytes())
        lost, added = raw - got, got - raw
        hyph = lost.pop("-", 0)     # 줄 끝 하이픈 잇기(SPEC §10.4 — "infor-"+"mation" → "information")로 빠진 '-'만 허용
        c.ok(not lost and not added, f"{name}: 원문 단어의 글자가 layout 결과(문단·표·그림 글자)에 빠짐·중복 없이 그대로"
             f"(줄 끝 하이픈 잇기 {hyph}개 제외)", f"빠짐 {dict(lost)} · 더함 {dict(added)}")
    if not real:
        return

    def tables(name: str, page: int) -> list:
        d = (pdfs / name).read_bytes()
        return [b for b in L.layout(d, page, page)["pages"][0]["blocks"] if b["type"] == "table"]

    if (pdfs / "real_mime_en.pdf").is_file():
        t = tables("real_mime_en.pdf", 5)
        rows = [[cell["text"] for cell in r] for r in t[0]["rows"]] if t else []
        c.ok(len(rows) == 4 and [r[0] for r in rows] == ["Attribute", "offset", "value", "mask"]
             and rows[1][2].startswith("The byte offset(s) in the file to check.") and rows[3][2].endswith("with 0x."),
             "real_mime_en 5쪽: 여러 줄 셀이 한 셀로(4행: Attribute·offset·value·mask)", str(rows)[:300])
        t = tables("real_mime_en.pdf", 4)
        c.ok(t and t[0]["rows"][1][2]["text"].startswith("string , host16 , host32 , big16"),
             "real_mime_en 4쪽: 셀 안 낱말 순서(쉼표가 앞으로 몰리지 않음)", str(t[0]["rows"][1] if t else None))
    if (pdfs / "real_xz_ko.pdf").is_file():
        t = tables("real_xz_ko.pdf", 13)
        c.ok(t and [cell["text"] for cell in t[0]["rows"][0]] == ["Preset", "DictSize", "CompCPU", "CompMem", "DecMem"]
             and [cell["text"] for cell in t[0]["rows"][1]] == ["-0e", "256 KiB", "8", "4 MiB", "1 MiB"],
             "real_xz_ko 13쪽 Preset 표: 머리글이 열과 어긋나도 5열", str(t[0]["rows"][:2] if t else None))
    if (pdfs / "real_rpi_text.pdf").is_file():
        d = (pdfs / "real_rpi_text.pdf").read_bytes()
        caps = 0
        for f in range(1, 291, 10):
            for p in L.layout(d, f, None, 12.0)["pages"]:
                for b in p["blocks"]:
                    if b["type"] == "table" and b["rows"][0][0]["text"].startswith("Figure"):
                        caps += 1
        c.ok(caps == 0, "real_rpi_text: 나란한 그림 설명('Figure 1-2 | Figure 1-3')을 표로 잡지 않음", f"{caps}개")


def main() -> int:
    ap = argparse.ArgumentParser(description="pdf_layout 모듈 점검 (모델 호출 0회)")
    ap.add_argument("--pdfs", default=str(Path(tempfile.gettempdir()) / "pdf-summary-test-pdfs"),
                    help="시험 PDF 폴더(저장소 밖). 없으면 만든다")
    ap.add_argument("--real", action="store_true", help="실제 문서(real_*.pdf)도 시험하고 결과를 기록")
    args = ap.parse_args()
    pdfs = Path(args.pdfs).expanduser().resolve()
    if mtp._inside_repo(pdfs):
        print("--pdfs는 저장소 밖이어야 합니다.", file=sys.stderr)
        return 2
    need = list(mtp.CASES) + list(mtp.LAYOUT_CASES)
    if not all((pdfs / n).is_file() for n in need):
        print(f"시험 PDF 만들기 → {pdfs}")
        if subprocess.run([sys.executable, "-B", str(TOOLS_DIR / "make_test_pdfs.py"), "--out", str(pdfs)]).returncode:
            return 1

    tmp = Path(tempfile.gettempdir())
    before_tmp = {p.name for p in tmp.iterdir() if p.name.startswith("pdf-layout-")}
    before_app = snapshot([APP_DIR])
    c = Checker()
    timings: dict = {}
    report: dict = {}
    t0 = time.monotonic()
    check_layout_cases(c, pdfs, timings)
    check_existing_cases(c, pdfs, timings)
    check_ranges(c, pdfs)
    check_limits(c, pdfs)
    check_export(c, pdfs, timings)
    check_file_names(c)
    check_tools_missing(c, pdfs)
    check_stop_and_tables(c, pdfs, args.real)
    if args.real:
        check_real(c, pdfs, timings, report)

    print("\n[디스크]")
    left = {p.name for p in tmp.iterdir() if p.name.startswith("pdf-layout-")} - before_tmp
    c.ok(not left, "도구 임시 폴더(pdf-layout-*)가 남지 않음", str(sorted(left)[:5]))
    new_app = sorted(snapshot([APP_DIR]) - before_app)
    new_app = [p for p in new_app if "__pycache__" not in p]
    c.ok(not new_app, "앱 폴더에 새 파일 없음(도구가 작업 폴더에 쓴 것 포함)", str(new_app[:5]))

    print("\n[시간]")
    for k, v in timings.items():
        print(f"  {k}: {v:.0f}KB" if k.endswith("KB") else f"  {k}: {v:.2f}s")
    if report:
        print("\n[실제 문서 결과]")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n결과: 통과 {c.passed} · 실패 {len(c.failed)} · {time.monotonic() - t0:.1f}s")
    for f in c.failed:
        print(f"  실패: {f}")
    return 1 if c.failed else 0


if __name__ == "__main__":
    sys.exit(main())
