"""PDF 바이트 → 쪽 구조(글자·제목·표·그림·목차)와 내려받을 HTML 한 파일. 모델을 부르지 않는다.

    import pdf_layout
    doc = pdf_layout.layout(pdf_bytes, first=1, last=5)          # SPEC §10.4의 dict
    html_text, stats = pdf_layout.export_html(pdf_bytes, "보고서.pdf")   # SPEC §10.6
    pdf_layout.export_file_name("보고서.pdf")                     # "보고서_추출.html"

쓰는 도구(poppler-utils, PRD D19·D20):
- `pdfinfo`            쪽수·제목·쪽 크기(회전 반영)
- `pdftotext -bbox-layout`  단어와 좌표(pt). **글자는 여기서만** 가져온다(한국어 띄어쓰기가 정확하다)
- `pdftohtml -xml -i`   글자 크기·굵기(좌표로 줄에 맞춤)와 책갈피. 이 출력의 글자는 쓰지 않는다(chromium 한국어 PDF에서 띄어쓰기가 사라진다)
- `pdftohtml -xml -dataurls`  그림 상자 위치만(`src`는 버린다). 범위 전체가 죽으면 쪽마다 다시, 그래도 죽으면 그 쪽만 `figures_failed`
- `pdftoppm`            그림 상자 영역을 96dpi로 다시 렌더(stdout)

디스크·보안:
- 입력은 모두 stdin, 출력은 stdout. 그래도 도구가 작업 폴더에 파일을 쓸 수 있으므로(실측: pdftoppm에 출력 이름 `-`를 주면
  `--014.png`를 씀) **도구를 실행할 때마다 새 빈 임시 폴더를 작업 폴더로 주고 끝나면 지운다.** 무언가 남아 있었으면
  stderr(서버 로그)에 코드 `STRAY_FILES`만 찍는다.
- `shell=True` 없이 고정 인자 목록. 사용자 입력(파일 이름 등)은 인자로 넘기지 않는다.
- 내려받을 HTML은 문서에서 온 모든 문자열을 `html.escape`하고 스크립트가 없으며 CSP 메타로 스크립트·외부 요청을 막는다.

예외는 `pdf_extract.ExtractError`(와 그 하위 `LayoutError`)뿐이다. 쪽 범위가 틀리면 code `BAD_RANGE`(서버가 400 BAD_REQUEST로).
`should_stop`(인자 없는 함수)을 주면 쪽·그림마다 불러 참이면 code `CLIENT_GONE`으로 멈춘다(서버가 요청자가 떠났는지 알려 준다).
계약: SPEC.md §10.2(수치)·§10.4(JSON)·§10.5(처리 순서)·§10.6(HTML).
"""

from __future__ import annotations

import base64
import html
import re
import shutil
import subprocess
import sys
import tempfile
import time
import os
import unicodedata

import pdf_extract
from pdf_extract import ExtractError

# ── 수치 (SPEC §10.2, 모두 [제안] — 시험이 바꿔 끼울 수 있게 모듈 상수) ──
LAYOUT_PAGES_PER_REQ: int = 10
FIGURE_DPI: int = 96
FIGURE_MIN_PT: float = 20              # 가로·세로 모두 이보다 작은 그림은 버린다(글머리표·아이콘)
FIGURE_PNG_MAX_BYTES: int = 200 * 1024  # PNG가 이보다 크면 JPEG로 다시 렌더
FIGURE_JPEG_QUALITY: int = 85
FIGURES_PER_PAGE: int = 30
LAYOUT_FIGURE_BYTES: int = 8 * 1024 * 1024    # 요청 하나의 그림 바이트 합(base64 전)
EXPORT_FIGURE_BYTES: int = 30 * 1024 * 1024   # 내려받을 파일 하나의 그림 바이트 합
BACKGROUND_AREA: float = 0.5           # 쪽 면적의 이만큼 이상을 덮고 안에 글자가 있는 그림은 배경으로 보고 뺀다
OUTLINE_MAX: int = 2000
LAYOUT_BUDGET_S: float = 60
EXPORT_BUDGET_S: float = 150
BODY_SIZE_MIN: float = 4.0
BODY_SIZE_MAX: float = 72.0

# 제목 판정(본문 크기와의 비) — SPEC §10.1
H1_RATIO, H2_RATIO, H3_RATIO = 1.6, 1.3, 1.15
HEADING_MAX_CHARS = 200          # 이보다 긴 블록은 글자가 커도 문단으로 본다
BOLD_H3_MAX_CHARS = 60
# 표 감지 — SPEC §10.5 6단계
TABLE_GAP_RATIO = 0.6            # 행 안에서 단어 간격이 줄 높이 × 이 값 이상이면 칸 경계
TABLE_ROW_GAP_RATIO = 2.0        # 행 사이 간격이 줄 높이 × 이 값 이하여야 같은 표
TABLE_ALIGN_PT = 4.0             # 열 정렬 허용 오차
TABLE_MAX_CELL_CHARS = 40        # 셀 글자 평균이 이보다 크면 2단 본문
TABLE_MAX_CELL_WORDS = 6         # 셀 단어 평균이 이보다 크면 2단 본문(한국어는 한 단 한 줄이 20자 안팎이라 글자 수만으로는 못 가른다)
TABLE_PARA_SHARE = 0.5           # 표 후보 줄의 이 비율 이상이 3줄 이상 문단 블록에서 왔으면 2단 본문
_CAPTION_RE = re.compile(r"(?:figure|fig\.|table|그림|표|사진)(?:\s|\d|\Z)", re.I)   # 그림 설명 머리("Figure 1-2")

TOOLS = ("pdftotext", "pdfinfo", "pdftohtml", "pdftoppm")
TITLE_MAX = 200

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_TAG_RE = re.compile(r"<[^>]*>")
_ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
_DATA_URI_RE = re.compile(r"\Adata:image/(?:png|jpeg);base64,[A-Za-z0-9+/]+={0,2}\Z")
_FNAME_BAD_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f-\x9f]')
_EDGE_RE = re.compile(r"^[\s.]+|[\s.]+$")


class LayoutError(ExtractError):
    """ExtractError에 `BAD_RANGE`(쪽 범위 잘못)·`CLIENT_GONE`(요청자가 떠남)을 더한 것. isinstance(e, ExtractError)는 참."""

    def __init__(self, code: str, log: str = ""):
        super().__init__(code, log)
        self.code = code


def check_tools() -> dict[str, bool]:
    """{"pdftotext", "pdfinfo", "pdftohtml", "pdftoppm"} — shutil.which로만 확인하고 실행하지 않는다."""
    return {name: shutil.which(name) is not None for name in TOOLS}


# ── 도구 실행 ──────────────────────────────────────────────────────────

def _stray(tool: str) -> None:
    print(f"[pdf_layout] STRAY_FILES {tool}", file=sys.stderr, flush=True)


def _run(tool: str, args: list[str], data: bytes, timeout: float | None = None) -> subprocess.CompletedProcess:
    """도구를 stdin=data로, 새 빈 임시 폴더를 작업 폴더로 실행한다. 종료코드는 호출한 쪽이 본다."""
    path = shutil.which(tool)
    if path is None:
        raise ExtractError("TOOL_MISSING", f"{tool} 없음")
    limit = pdf_extract.EXTRACT_TIMEOUT_S if timeout is None else timeout
    with tempfile.TemporaryDirectory(prefix="pdf-layout-") as wd:
        try:
            return subprocess.run([path, *args], input=data, capture_output=True, timeout=limit,
                                  cwd=wd, check=False)
        except subprocess.TimeoutExpired:
            raise ExtractError("EXTRACT_TIMEOUT", f"{tool} {limit}s 초과") from None
        except OSError as exc:
            raise ExtractError("TOOL_MISSING", f"{tool} 실행 실패 {type(exc).__name__}") from None
        finally:
            try:
                if os.listdir(wd):
                    _stray(tool)
            except OSError:
                pass


def _run_ok(tool: str, args: list[str], data: bytes) -> bytes:
    proc = _run(tool, args, data)
    if proc.returncode != 0:
        if b"Incorrect password" in proc.stderr:
            raise ExtractError("ENCRYPTED", f"{tool} 종료코드 {proc.returncode}, 암호")
        raise ExtractError("BROKEN_PDF", f"{tool} 종료코드 {proc.returncode}")
    return proc.stdout


# ── 문자열 정리 ────────────────────────────────────────────────────────

def _clean(s: str) -> str:
    """NFC, \\n·\\t 외 제어문자 제거, 짝 없는 서로게이트는 U+FFFD로."""
    s = unicodedata.normalize("NFC", _CONTROL_RE.sub("", s))
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in s):
        s = s.encode("utf-8", "replace").decode("utf-8", "replace")
    return s


def _attrs(s: str) -> dict[str, str]:
    return dict(_ATTR_RE.findall(s))


def _num(v: str | None, default: float = 0.0) -> float:
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return f if f == f and abs(f) < 1e7 else default


def _r(v: float) -> float:
    return round(v + 0.0, 2)


# ── 1·2. 문서 정보 ─────────────────────────────────────────────────────

def _info(data: bytes) -> tuple[int, str, list[list[float]]]:
    raw = _run_ok("pdfinfo", ["-enc", "UTF-8", "-f", "1", "-l", str(pdf_extract.MAX_PAGES), "-"], data)
    pages: int | None = None
    title = ""
    sizes: dict[int, list[float]] = {}
    rots: dict[int, int] = {}
    size_re = re.compile(r"Page\s+(\d+)\s+size:\s+([\d.]+)\s+x\s+([\d.]+)")
    rot_re = re.compile(r"Page\s+(\d+)\s+rot:\s+(-?\d+)")
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if line.startswith("Pages:"):
            try:
                pages = int(line.split(":", 1)[1].strip())
            except ValueError:
                pages = None
        elif line.startswith("Title:"):
            title = line.split(":", 1)[1].strip()
        elif m := size_re.match(line):
            sizes[int(m.group(1))] = [_num(m.group(2)), _num(m.group(3))]
        elif m := rot_re.match(line):
            rots[int(m.group(1))] = int(m.group(2)) % 360
    if pages is None or pages < 0:
        raise ExtractError("BROKEN_PDF", "pdfinfo 출력에 Pages 없음")
    if pages > pdf_extract.MAX_PAGES:
        raise ExtractError("TOO_LONG", f"{pages}쪽 > {pdf_extract.MAX_PAGES}")
    if pages == 0:
        raise ExtractError("BROKEN_PDF", "쪽이 없음")
    page_sizes = []
    for n in range(1, pages + 1):
        w, h = sizes.get(n, [612.0, 792.0])
        if rots.get(n, 0) in (90, 270):
            w, h = h, w
        page_sizes.append([_r(w), _r(h)])
    return pages, _clean(title).strip()[:TITLE_MAX], page_sizes


def _check_input(data: bytes) -> None:
    if not data:
        raise ExtractError("EMPTY_FILE", "0바이트")
    if b"%PDF-" not in data[:1024]:
        raise ExtractError("NOT_PDF", "앞 1024바이트에 %PDF- 없음")
    missing = [name for name, ok in check_tools().items() if not ok]
    if missing:
        raise ExtractError("TOOL_MISSING", f"{', '.join(missing)} 없음")


# ── 3. 글자·좌표 (pdftotext -bbox-layout) ─────────────────────────────

_BB_RE = re.compile(r'<page\b([^>]*)>|<block\b[^>]*>|<line\b[^>]*>|<word\b([^>]*)>(.*?)</word>', re.S)


class Word:
    __slots__ = ("x0", "y0", "x1", "y1", "text", "line")

    def __init__(self, x0, y0, x1, y1, text):
        self.x0, self.y0, self.x1, self.y1, self.text = x0, y0, x1, y1, text
        self.line = None

    @property
    def cx(self):
        return (self.x0 + self.x1) / 2

    @property
    def cy(self):
        return (self.y0 + self.y1) / 2


class Line:
    __slots__ = ("words", "block", "size", "bold", "x0", "y0", "x1", "y1")

    def __init__(self, block):
        self.words: list[Word] = []
        self.block = block
        self.size = 0.0
        self.bold = False

    def box(self) -> None:
        self.x0 = min(w.x0 for w in self.words)
        self.y0 = min(w.y0 for w in self.words)
        self.x1 = max(w.x1 for w in self.words)
        self.y1 = max(w.y1 for w in self.words)

    @property
    def h(self):
        return max(self.y1 - self.y0, 0.1)

    @property
    def text(self):
        return " ".join(w.text for w in self.words)


class BPage:
    def __init__(self, w: float, h: float):
        self.w, self.h = w, h
        self.blocks: list[list[Line]] = []
        self.nlines: dict[int, int] = {}   # id(block) → 처음 줄 수(표 판정: 여러 줄 문단 블록인지)


def _parse_bbox(raw: bytes) -> list[BPage]:
    text = raw.decode("utf-8", errors="replace")
    pages: list[BPage] = []
    page = None
    block = None
    line = None
    for m in _BB_RE.finditer(text):
        tag = m.group(0)[:5]
        if tag == "<page":
            a = _attrs(m.group(1))
            page = BPage(_num(a.get("width"), 612), _num(a.get("height"), 792))
            pages.append(page)
            block = line = None
        elif page is None:
            continue
        elif tag == "<bloc":
            block = []
            page.blocks.append(block)
            line = None
        elif tag == "<line":
            if block is None:
                block = []
                page.blocks.append(block)
            line = Line(block)
            block.append(line)
        else:
            if line is None:
                if block is None:
                    block = []
                    page.blocks.append(block)
                line = Line(block)
                block.append(line)
            a = _attrs(m.group(2))
            word = _clean(html.unescape(m.group(3))).strip()
            if not word:
                continue
            w = Word(_num(a.get("xMin")), _num(a.get("yMin")), _num(a.get("xMax")), _num(a.get("yMax")), word)
            w.line = line
            line.words.append(w)
    for p in pages:
        blocks = []
        for b in p.blocks:
            b[:] = [ln for ln in b if ln.words]
            for ln in b:
                ln.box()
            if b:
                p.nlines[id(b)] = len(b)
                blocks.append(b)
        p.blocks = blocks
    return pages


# ── 4·5. 글자 크기·굵기·책갈피 (pdftohtml -xml -i) ─────────────────────

_HX_RE = re.compile(
    r'<page number="(\d+)"[^>]*>|<fontspec\b([^>]*)/>|<text\b([^>]*)>(.*?)</text>'
    r'|<outline>|</outline>|<item\b([^>]*)>(.*?)</item>', re.S)


class Fonts:
    """pdftohtml -xml -i 출력: 쪽마다 글자 조각(상자·크기·굵기·글자 수), 문서 책갈피, 크기별 글자 수."""

    def __init__(self):
        self.pieces: dict[int, list[tuple]] = {}
        self.outline: list[dict] = []
        self.chars_by_size: dict[float, int] = {}


def _parse_fonts(raw: bytes) -> Fonts:
    text = raw.decode("utf-8", errors="replace")
    f = Fonts()
    specs: dict[str, tuple[float, bool]] = {}
    page = 0
    depth = 0
    for m in _HX_RE.finditer(text):
        g = m.group(0)
        if g.startswith("<page"):
            page = int(m.group(1))
            f.pieces.setdefault(page, [])
        elif g.startswith("<fontspec"):
            a = _attrs(m.group(2))
            specs[a.get("id", "")] = (_num(a.get("size"), 0.0), "bold" in a.get("family", "").lower())
        elif g.startswith("<text"):
            a = _attrs(m.group(3))
            size, fbold = specs.get(a.get("font", ""), (0.0, False))
            inner = m.group(4)
            bold = fbold or "<b>" in inner
            n = sum(1 for ch in html.unescape(_TAG_RE.sub("", inner)) if not ch.isspace())
            x0, y0 = _num(a.get("left")), _num(a.get("top"))
            box = (x0, y0, x0 + _num(a.get("width")), y0 + _num(a.get("height")), size, bold)
            f.pieces.setdefault(page, []).append(box)
            if size > 0 and n:
                key = round(size, 1)
                f.chars_by_size[key] = f.chars_by_size.get(key, 0) + n
        elif g == "<outline>":
            depth += 1
        elif g == "</outline>":
            depth = max(0, depth - 1)
        elif g.startswith("<item"):
            a = _attrs(m.group(5))
            title = _clean(html.unescape(_TAG_RE.sub("", m.group(6)))).replace("\n", " ").replace("\t", " ").strip()
            f.outline.append({"title": title[:TITLE_MAX], "page": a.get("page"), "level": max(1, min(6, depth))})
    return f


def _fonts_args(first: int | None = None, last: int | None = None, *, images: bool = False) -> list[str]:
    args = ["-xml", "-stdout", "-q", "-zoom", "1", "-noroundcoord", "-enc", "UTF-8"]
    args += ["-dataurls"] if images else ["-i"]
    if first is not None:
        args += ["-f", str(first), "-l", str(last)]
    return args + ["-", "x"]    # [실측] stdin이면 출력 이름이 있어야 한다. -stdout이라 실제로 쓰지는 않는다


def _get_fonts(data: bytes, first: int | None, last: int | None) -> Fonts:
    """실패(0이 아닌 종료)하면 빈 Fonts — 모든 줄이 추정 크기·굵기 없음, 책갈피 없음(SPEC §10.5 4단계)."""
    try:
        proc = _run("pdftohtml", _fonts_args(first, last), data)
    except ExtractError as exc:
        if exc.code in ("TOOL_MISSING",):
            raise
        return Fonts()
    if proc.returncode != 0:
        return Fonts()
    return _parse_fonts(proc.stdout)


def _body_size(fonts: Fonts) -> float:
    if not fonts.chars_by_size:
        return 12.0
    best = max(fonts.chars_by_size.items(), key=lambda kv: (kv[1], -kv[0]))[0]
    return float(best) if BODY_SIZE_MIN <= best <= BODY_SIZE_MAX else 12.0


def _outline(fonts: Fonts, pages: int) -> list[dict]:
    out = []
    for item in fonts.outline:
        try:
            page = int(item["page"])
        except (TypeError, ValueError):
            continue
        if not 1 <= page <= pages or not item["title"]:
            continue
        out.append({"title": item["title"], "page": page, "level": item["level"]})
        if len(out) >= OUTLINE_MAX:
            break
    return out


# ── 7. 그림 위치 (pdftohtml -xml -dataurls) ───────────────────────────

_IMG_RE = re.compile(r'<page number="(\d+)"|<image\b([^>]*)>')
_IMG_ATTR_RE = re.compile(r'\b(top|left|width|height)="([-\d.eE+]+)"')


def _parse_images(raw: bytes) -> dict[int, list[tuple[float, float, float, float]]]:
    text = raw.decode("latin-1")      # 속성만 쓴다. src(base64)는 버린다
    out: dict[int, list] = {}
    page = 0
    for m in _IMG_RE.finditer(text):
        if m.group(1) is not None:
            page = int(m.group(1))
            out.setdefault(page, [])
            continue
        head = m.group(2).split(" src=", 1)[0]
        a = dict(_IMG_ATTR_RE.findall(head))
        out.setdefault(page, []).append((_num(a.get("left")), _num(a.get("top")),
                                         _num(a.get("width")), _num(a.get("height"))))
    return out


# ── 7-b. -dataurls가 죽는 쪽의 대안: pdftocairo -svg (stdout) ──────────
# [실측 2026-11-18] poppler 25.03의 `pdftohtml -dataurls`는 실제 안내서뿐 아니라 chromium이 만든 평범한 PNG 그림 쪽
# (figs.pdf 2쪽)·gs로 만든 스캔 쪽(mixed.pdf 2쪽)에서도 쪽 하나만으로 세그멘테이션 오류/double free로 죽는다.
# 그대로 두면 스캔한 쪽·큰 그림이 모두 빠지므로, 그 쪽만 pdftocairo -svg를 stdout으로 받아 그림 상자를 계산한다
# (SPEC §10.5 7단계에서 벗어남 — 보고함). 이것도 실패하면 그때 figures_failed.
# cairo SVG: <image id="source-N" width height> 를 <use xlink:href="#source-N" transform="matrix(..)">로 놓고,
# 잘라 낸 그림은 <g clip-path="url(#clip-N)"> 안에 음수 오프셋으로 놓는다 → 보이는 상자 = 변환한 사각형 ∩ 잘라내기 상자.

_SVG_TOK = re.compile(r"<(/?)(svg|defs|mask|pattern|clipPath|symbol|g|use|image|path|rect)\b([^>]*?)(/?)>", re.S)
_SVG_HIDDEN = {"defs", "mask", "pattern", "clipPath", "symbol"}
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
_TF_RE = re.compile(r"(matrix|translate|scale)\(([^)]*)\)")

_IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _mul(p: tuple, c: tuple) -> tuple:
    a, b, cc, d, e, f = p
    a2, b2, c2, d2, e2, f2 = c
    return (a * a2 + cc * b2, b * a2 + d * b2, a * c2 + cc * d2, b * c2 + d * d2,
            a * e2 + cc * f2 + e, b * e2 + d * f2 + f)


def _transform(attr: str) -> tuple:
    m = _IDENT
    for kind, args in _TF_RE.findall(attr):
        v = [float(x) for x in _NUM_RE.findall(args)]
        if kind == "matrix" and len(v) == 6:
            t = tuple(v)
        elif kind == "translate" and v:
            t = (1.0, 0.0, 0.0, 1.0, v[0], v[1] if len(v) > 1 else 0.0)
        elif kind == "scale" and v:
            t = (v[0], 0.0, 0.0, v[1] if len(v) > 1 else v[0], 0.0, 0.0)
        else:
            continue
        m = _mul(m, t)
    return m


def _box(m: tuple, x0: float, y0: float, x1: float, y1: float) -> tuple:
    pts = [(m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _clip(a: tuple | None, b: tuple | None) -> tuple | None:
    if a is None:
        return b
    if b is None:
        return a
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))


def _parse_svg_images(raw: bytes) -> list[tuple[float, float, float, float]]:
    """cairo SVG → 보이는 그림 상자 [(x, y, w, h)] (pt, 쪽 왼쪽 위 원점)."""
    text = raw.decode("latin-1")
    images: dict[str, tuple] = {}         # id → (x, y, w, h) 그림 자기 좌표
    clips: dict[str, tuple] = {}          # id → 상자(clipPath 좌표계)
    groups: dict[str, list] = {}          # id 있는 숨은 g(합성 그룹) → 그 g 좌표계의 보이는 상자들
    # 쌓임: (tag, 현재 행렬, 잘라내기 상자, 숨김?, 모으는 그룹 id, 그 그룹 기준 행렬)
    stack: list[tuple] = [("root", _IDENT, None, False, None, _IDENT)]
    clip_id = None
    clip_pts: list[float] = []
    out: list[tuple] = []

    def emit(box, clipbox, collect):
        if clipbox is not None:
            box = _clip(box, clipbox)
        if box[2] - box[0] <= 0 or box[3] - box[1] <= 0:
            return
        if collect is not None:
            groups.setdefault(collect, []).append(box)
        else:
            out.append(box)

    for m in _SVG_TOK.finditer(text):
        closing, tag, attrs, selfclose = m.group(1), m.group(2), m.group(3), m.group(4)
        if closing:
            if tag == "clipPath" and clip_id is not None:
                if clip_pts:
                    xs, ys = clip_pts[0::2], clip_pts[1::2]
                    clips[clip_id] = (min(xs), min(ys), max(xs), max(ys))
                clip_id = None
            # 맞는 여는 태그까지 되돌린다(태그 짝이 어긋나도 멈추지 않게)
            for k in range(len(stack) - 1, 0, -1):
                if stack[k][0] == tag:
                    del stack[k:]
                    break
            continue
        head = attrs.split('xlink:href="data:', 1)[0]
        tag_, mat, clipbox, hidden, collect, rel = stack[-1]
        a = _attrs(head)
        if tag == "image":
            if "id" in a:
                images[a["id"]] = (_num(a.get("x")), _num(a.get("y")), _num(a.get("width")), _num(a.get("height")))
            continue
        if tag in ("path", "rect"):
            if clip_id is not None:
                if tag == "rect":
                    x, y = _num(a.get("x")), _num(a.get("y"))
                    clip_pts += [x, y, x + _num(a.get("width")), y + _num(a.get("height"))]
                else:
                    nums = [float(v) for v in _NUM_RE.findall(a.get("d", ""))]
                    clip_pts += nums[: len(nums) // 2 * 2]
            continue
        own = _transform(a.get("transform", ""))
        if tag == "use":
            ref = a.get("xlink:href", a.get("href", "")).lstrip("#")
            base = rel if collect is not None else mat
            tm = _mul(base, own)
            if "x" in a or "y" in a:
                tm = _mul(tm, (1.0, 0.0, 0.0, 1.0, _num(a.get("x")), _num(a.get("y"))))
            if hidden and collect is None:
                continue
            if ref in images:
                x, y, w, h = images[ref]
                emit(_box(tm, x, y, x + w, y + h), clipbox, collect)
            elif ref in groups:
                for (x0, y0, x1, y1) in list(groups[ref]):
                    emit(_box(tm, x0, y0, x1, y1), clipbox, collect)
            continue
        # 여는 태그: svg·defs·mask·pattern·clipPath·symbol·g
        new_mat = _mul(mat, own)
        new_rel = _mul(rel, own)
        new_hidden = hidden or tag in _SVG_HIDDEN
        new_collect = collect
        if tag == "g" and "id" in a and new_hidden:
            new_collect, new_rel = a["id"], _IDENT      # 합성 그룹: 자기 좌표계로 모아 두었다가 <use>에서 놓는다
        new_clip = clipbox
        cref = re.match(r"url\(#([^)]+)\)", a.get("clip-path", ""))
        if cref and cref.group(1) in clips:
            cm = new_rel if new_collect is not None else new_mat
            new_clip = _clip(clipbox, _box(cm, *clips[cref.group(1)]))
        if tag == "clipPath":
            clip_id, clip_pts = a.get("id"), []
        if not selfclose:
            stack.append((tag, new_mat, new_clip, new_hidden, new_collect, new_rel))
    return [(x0, y0, x1 - x0, y1 - y0) for (x0, y0, x1, y1) in out]


def _svg_images(data: bytes, page: int, deadline: float) -> list | None:
    if shutil.which("pdftocairo") is None or time.monotonic() >= deadline:
        return None
    try:
        proc = _run("pdftocairo", ["-svg", "-f", str(page), "-l", str(page), "-", "-"], data,
                    timeout=min(pdf_extract.EXTRACT_TIMEOUT_S, max(1.0, deadline - time.monotonic())))
    except ExtractError:
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    try:
        return _parse_svg_images(proc.stdout)
    except (ValueError, IndexError, OverflowError):
        return None


def _get_images(data: bytes, first: int, last: int, deadline: float) -> dict[int, list | None]:
    """쪽 → 그림 상자 목록. None이면 그 쪽은 위치를 끝내 얻지 못함(figures_failed).

    [실측] -dataurls는 그림 많은 문서 전체로 돌리면 세그멘테이션 오류로 죽고 쪽 단위로는 일부 쪽만 죽는다
    → 범위 전체로 한 번, 실패하면 쪽마다 다시, 그 쪽도 죽으면 pdftocairo -svg로(위 7-b).
    """
    result: dict[int, list | None] = {}

    def attempt(f: int, l: int, *, timed: bool = True) -> dict | None:
        if timed and time.monotonic() >= deadline:
            return None
        limit = pdf_extract.EXTRACT_TIMEOUT_S
        if timed:
            limit = min(limit, max(1.0, deadline - time.monotonic()))
        try:
            proc = _run("pdftohtml", _fonts_args(f, l, images=True), data, timeout=limit)
        except ExtractError as exc:
            if exc.code == "TOOL_MISSING":
                raise
            return None
        if proc.returncode != 0:
            return None
        return _parse_images(proc.stdout)

    # 범위 전체 한 번은 예산과 관계없이(그림 자리는 쪽 구조의 일부다 — 예산이 지나면 픽셀만 TIME으로 뺀다)
    whole = attempt(first, last, timed=False)
    if whole is not None:
        for p in range(first, last + 1):
            result[p] = whole.get(p, [])
        return result
    for p in range(first, last + 1):
        one = attempt(p, p) if first != last else None
        result[p] = one.get(p, []) if one is not None else _svg_images(data, p, deadline)
    return result


# ── 6. 쪽 하나 만들기 (글자 꾸밈·그림·표·제목) ─────────────────────────

def _style_lines(lines: list[Line], pieces: list[tuple]) -> None:
    """bbox 줄마다 세로로 50% 이상 겹치고 가로로 겹치는 조각 중 겹친 넓이가 가장 큰 것의 크기·굵기."""
    for ln in lines:
        best = None
        best_area = 0.0
        for (x0, y0, x1, y1, size, bold) in pieces:
            oy = min(ln.y1, y1) - max(ln.y0, y0)
            if oy <= 0 or oy < 0.5 * min(ln.h, max(y1 - y0, 0.1)):
                continue
            ox = min(ln.x1, x1) - max(ln.x0, x0)
            if ox <= 0:
                continue
            if ox * oy > best_area:
                best_area = ox * oy
                best = (size, bold)
        if best is not None and best[0] > 0:
            ln.size, ln.bold = best
        else:
            ln.size, ln.bold = ln.h / 1.15, False


class Fig:
    __slots__ = ("x0", "y0", "x1", "y1", "words", "src", "omitted", "block")

    def __init__(self, x0, y0, x1, y1):
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        self.words: list[Word] = []
        self.src = None
        self.omitted = None
        self.block: dict | None = None

    def contains(self, w: Word) -> bool:
        return self.x0 <= w.cx <= self.x1 and self.y0 <= w.cy <= self.y1


def _figures(bp: BPage, boxes: list, all_words: list[Word]) -> tuple[list[Fig], int]:
    figs: list[Fig] = []
    seen = set()
    skipped = 0
    area_page = bp.w * bp.h
    for (x, y, w, h) in boxes:
        if w < 0:                  # [실측] 회전한 쪽에서 pdftohtml이 음수 높이·폭을 낸다
            x, w = x + w, -w
        if h < 0:
            y, h = y + h, -h
        x0, y0 = max(0.0, x), max(0.0, y)
        x1, y1 = min(bp.w, x + w), min(bp.h, y + h)
        if x1 - x0 < 1 or y1 - y0 < 1:
            continue
        if x1 - x0 < FIGURE_MIN_PT and y1 - y0 < FIGURE_MIN_PT:
            continue
        key = (round(x0), round(y0), round(x1), round(y1))
        if key in seen:
            continue
        seen.add(key)
        fig = Fig(x0, y0, x1, y1)
        if (x1 - x0) * (y1 - y0) >= BACKGROUND_AREA * area_page and any(fig.contains(wd) for wd in all_words):
            skipped += 1
            continue
        figs.append(fig)
    figs.sort(key=lambda f: (f.y0, f.x0))
    return figs, skipped


def _cells(words: list[Word], h: float) -> list[list[Word]]:
    """x 순으로 놓인 단어를 간격(줄 높이 × TABLE_GAP_RATIO 이상)으로 나눈 칸."""
    cells: list[list[Word]] = []
    for w in words:
        if cells and w.x0 - cells[-1][-1].x1 < h * TABLE_GAP_RATIO:
            cells[-1].append(w)
        else:
            cells.append([w])
    return cells


def _aligned(vals: list[float]) -> bool:
    vals = sorted(vals)
    return any(b - a <= TABLE_ALIGN_PT for a, b in zip(vals, vals[1:]))


def _tables(bp: BPage, lines: list[Line]) -> list[tuple[list[Line], dict]]:
    """한 쪽의 줄에서 표를 찾는다 → [(표가 된 줄들, table 블록 dict(좌표·rows·cols·size))].

    칸이 2개 이상인 행(기준 행)이 이어지면 표 후보다. 그 사이·뒤에 칸이 하나뿐이고 첫 열이 비어 있는(둘째 열 이후에서
    시작하는) 줄은 바로 위 행의 셀이 여러 줄로 이어진 것으로 보고 같은 행에 붙인다(4단계 QA — 실제 문서의 여러 줄 셀).
    """
    if len(lines) < 2:
        return []
    # 세로로 겹치는 줄을 한 행으로(서로 다른 bbox 블록이라도)
    rows: list[dict] = []
    for ln in sorted(lines, key=lambda l: (l.y0, l.x0)):
        if rows:
            r = rows[-1]
            ov = min(r["y1"], ln.y1) - max(r["y0"], ln.y0)
            if ov >= 0.5 * min(r["h"], ln.h):
                r["lines"].append(ln)
                r["y0"], r["y1"] = min(r["y0"], ln.y0), max(r["y1"], ln.y1)
                r["h"] = min(r["h"], ln.h)
                continue
        rows.append({"lines": [ln], "y0": ln.y0, "y1": ln.y1, "h": ln.h})
    for r in rows:
        words = sorted((w for ln in r["lines"] for w in ln.words), key=lambda w: w.x0)
        r["cells"] = _cells(words, r["h"])
        r["cont"] = False

    def extend(i: int, allow_cont: bool) -> int:
        starts = [c[0].x0 for c in rows[i]["cells"][1:]]
        j = i + 1
        while j < len(rows):
            r, prev = rows[j], rows[j - 1]
            r["cont"] = False
            if r["y0"] - prev["y1"] > TABLE_ROW_GAP_RATIO * max(prev["h"], r["h"]):
                break
            if len(r["cells"]) >= 2:
                starts += [c[0].x0 for c in r["cells"][1:]]
            elif allow_cont and r["cells"] and r["cells"][0][0].x0 >= min(starts) - TABLE_ALIGN_PT:
                r["cont"] = True         # 첫 열이 빈 한 칸 줄: 위 행 셀의 다음 줄
            else:
                break
            j += 1
        return j

    found = []
    i = 0
    while i < len(rows):
        if len(rows[i]["cells"]) < 2:
            i += 1
            continue
        rows[i]["cont"] = False
        j = extend(i, True)
        group = rows[i:j]
        tbl = _table_from_rows(bp, group) if sum(1 for r in group if not r["cont"]) >= 2 else None
        if tbl is None and any(r["cont"] for r in group):
            # 이어진 줄을 붙여서는 표가 아니면 예전 규칙(칸 2개 이상인 행만 이어짐)으로 다시
            j = extend(i, False)
            group = rows[i:j]
            tbl = _table_from_rows(bp, group) if len(group) >= 2 else None
        if tbl is not None:
            found.append(tbl)
        i = j
    return found


def _project(rows: list[dict]) -> list[list[float]]:
    """칸 상자를 x축에 투영해 겹치는 것끼리 묶으면 열이 된다."""
    spans = sorted((c[0].x0, c[-1].x1) for r in rows for c in r["cells"])
    cols: list[list[float]] = []
    for x0, x1 in spans:
        if cols and x0 < cols[-1][1] + 1:
            cols[-1][1] = max(cols[-1][1], x1)
        else:
            cols.append([x0, x1])
    return cols


def _table_from_rows(bp: BPage, group: list[dict]) -> tuple[list[Line], dict] | None:
    anchors = [r for r in group if not r["cont"]]
    cols = _project(anchors)
    if len(anchors) >= 3:
        # 머리글이 아래 열과 어긋나 두 열에 걸치면(실제 문서 xz의 Preset 표) 머리글을 빼고 열을 잡는다
        alt = _project(anchors[1:])
        if len(alt) > len(cols):
            cols = alt
    if len(cols) < 2:
        return None

    def col_of(w: Word) -> int:
        for k, (a, b) in enumerate(cols):
            if a - 1 <= w.cx <= b + 1:
                return k
        return min(range(len(cols)), key=lambda k: abs(cols[k][0] - w.x0))

    grid: list[list[list[Word]]] = []
    phys: list[list[list[Word]]] = []     # 기준 행의 첫 줄 칸(2단 본문 판정은 이것으로 — 여러 줄 셀의 긴 설명은 빼고)
    for r in group:
        row: list[list[Word]] = [[] for _ in cols]
        if not r["cont"] and len(r["cells"]) == len(cols):
            for k, c in enumerate(r["cells"]):     # 칸 수가 열 수와 같으면 순서대로(머리글이 열과 어긋나도)
                row[k].extend(c)
        else:
            for c in r["cells"]:
                for w in c:
                    row[col_of(w)].append(w)
        if not r["cont"]:
            phys.append([list(cell) for cell in row])
        if r["cont"] and grid:
            for k in range(len(cols)):
                grid[-1][k].extend(row[k])
            continue
        if sum(1 for cell in row if cell) < 2:
            return None
        grid.append(row)

    # 열 정렬: 왼쪽·오른쪽·가운데 중 하나가 ±TABLE_ALIGN_PT 안에서 2행 이상 맞는 열이 2개 이상
    aligned = 0
    for k in range(len(cols)):
        cells = [row[k] for row in grid if row[k]]
        if len(cells) < 2:
            continue
        lefts = [min(w.x0 for w in c) for c in cells]
        rights = [max(w.x1 for w in c) for c in cells]
        centers = [(a + b) / 2 for a, b in zip(lefts, rights)]
        if _aligned(lefts) or _aligned(rights) or _aligned(centers):
            aligned += 1
    if aligned < 2:
        return None

    # 글머리표 목록(첫 열이 •·▶·- 같은 표시뿐)은 표가 아니다
    first_col = [" ".join(w.text for w in row[0]) for row in grid if row[0]]
    if first_col and all(len(t) <= 2 and not any(ch.isalnum() for ch in t) for t in first_col):
        return None
    # 나란히 놓인 그림 설명("Figure 1-2 | Figure 1-3" 다음 줄에 설명 둘)은 표가 아니다(4단계 QA, 실제 안내서 20건)
    head = [" ".join(w.text for w in cell) for cell in grid[0] if cell]
    if head and _CAPTION_RE.match(head[0]):
        return None
    # 칸의 절반 이상이 빈 표는 그림 속 블록·흩어진 낱말이다(실제 안내서의 Scratch 코드 그림)
    if sum(1 for row in grid for cell in row if not cell) * 2 >= len(grid) * len(cols):
        return None
    filled = [cell for row in phys for cell in row if cell]
    texts = [" ".join(w.text for w in cell) for cell in filled]
    if sum(len(t) for t in texts) / len(texts) > TABLE_MAX_CELL_CHARS:
        return None
    if sum(len(cell) for cell in filled) / len(filled) > TABLE_MAX_CELL_WORDS:
        return None
    anchor_lines = [ln for r in anchors for ln in r["lines"]]
    para = sum(1 for ln in anchor_lines if bp.nlines.get(id(ln.block), 1) >= 3)
    if para >= TABLE_PARA_SHARE * len(anchor_lines):
        return None

    out_rows = []
    for row in grid:
        out_row = []
        for cell in row:
            # 줄 순서 → 줄 안 x 순서(낱말 y로 정렬하면 쉼표처럼 위치가 다른 글자가 앞으로 온다 — 4단계 QA)
            cell = sorted(cell, key=lambda w: (w.line.y0, w.x0))
            out_row.append({"text": " ".join(w.text for w in cell),
                            "bold": bool(cell) and sum(1 for w in cell if w.line.bold) * 2 > len(cell)})
        out_rows.append(out_row)
    lines = [ln for r in group for ln in r["lines"]]
    words = [w for row in grid for cell in row for w in cell]
    x0, y0 = min(w.x0 for w in words), min(w.y0 for w in words)
    x1, y1 = max(w.x1 for w in words), max(w.y1 for w in words)
    block = {"type": "table", "x": _r(x0), "y": _r(y0), "w": _r(x1 - x0), "h": _r(y1 - y0),
             "size": _r(_dominant_size(lines)),
             "cols": [_r(min(w.x0 for row in grid for w in row[k]) if any(row[k] for row in grid) else cols[k][0])
                      for k in range(len(cols))],
             "rows": out_rows}
    return lines, block


def _dominant_size(lines: list[Line]) -> float:
    count: dict[float, int] = {}
    for ln in lines:
        key = round(ln.size, 1)
        count[key] = count.get(key, 0) + sum(len(w.text) for w in ln.words)
    return max(count.items(), key=lambda kv: (kv[1], kv[0]))[0] if count else 12.0


_LATIN_HYPHEN_END = re.compile(r"[A-Za-z]-$")
_LATIN_LOWER_START = re.compile(r"^[a-z]")


def _join_lines(texts: list[str]) -> str:
    out = ""
    for t in texts:
        if not out:
            out = t
        elif _LATIN_HYPHEN_END.search(out) and _LATIN_LOWER_START.match(t):
            out = out[:-1] + t
        else:
            out += " " + t
    return out


def _role(size: float, bold: bool, nlines: int, text: str, body: float) -> str:
    if len(text) > HEADING_MAX_CHARS or not any(ch.isalnum() for ch in text):
        return "p"
    r = size / body if body > 0 else 1.0
    if r >= H1_RATIO:
        return "h1"
    if r >= H2_RATIO:
        return "h2"
    if r >= H3_RATIO:
        return "h3"
    if bold and r >= 0.97 and nlines == 1 and len(text) <= BOLD_H3_MAX_CHARS:
        return "h3"
    return "p"


def _text_block(lines: list[Line], body: float) -> dict:
    texts = [ln.text for ln in lines]
    text = _join_lines(texts)
    size = _dominant_size(lines)
    bold = sum(len(ln.text) for ln in lines if ln.bold) * 2 > sum(len(t) for t in texts)
    x0, y0 = min(ln.x0 for ln in lines), min(ln.y0 for ln in lines)
    x1, y1 = max(ln.x1 for ln in lines), max(ln.y1 for ln in lines)
    return {"type": "text", "role": _role(size, bold, len(lines), text, body),
            "x": _r(x0), "y": _r(y0), "w": _r(x1 - x0), "h": _r(y1 - y0), "text": text,
            "lines": [{"x": _r(ln.x0), "y": _r(ln.y0), "w": _r(ln.x1 - ln.x0), "h": _r(ln.y1 - ln.y0),
                       "size": _r(ln.size), "bold": bool(ln.bold), "text": t} for ln, t in zip(lines, texts)]}


def _split_by_size(lines: list[Line]) -> list[list[Line]]:
    """한 bbox 블록 안에서 글자 크기가 10% 넘게 바뀌는 곳에서 나눈다(제목과 본문이 한 블록에 붙은 경우)."""
    parts: list[list[Line]] = []
    for ln in lines:
        if parts:
            prev = parts[-1][-1]
            a, b = max(prev.size, 0.1), max(ln.size, 0.1)
            if max(a, b) / min(a, b) <= 1.1:
                parts[-1].append(ln)
                continue
        parts.append([ln])
    return parts


def _build_page(number: int, bp: BPage, pieces: list[tuple], boxes: list | None, body: float) -> tuple[dict, list[Fig]]:
    lines_all = [ln for b in bp.blocks for ln in b]
    _style_lines(lines_all, pieces)
    words_all = [w for ln in lines_all for w in ln.words]

    figs: list[Fig] = []
    skipped = 0
    if boxes:
        figs, skipped = _figures(bp, boxes, words_all)
    # 그림 안에 중심이 들어가는 단어는 글자 층에서 빼서 그림 설명으로(SPEC §10.5 7단계, 미결 U20 (a))
    if figs:
        for ln in lines_all:
            keep = []
            for w in ln.words:
                for f in figs:
                    if f.contains(w):
                        f.words.append(w)
                        break
                else:
                    keep.append(w)
            if len(keep) != len(ln.words):
                ln.words = keep
                if keep:
                    ln.box()
        for b in bp.blocks:
            b[:] = [ln for ln in b if ln.words]
        bp.blocks = [b for b in bp.blocks if b]
        lines_all = [ln for b in bp.blocks for ln in b]

    # 표: 표가 된 줄은 원래 블록에서 빼고, 그 줄들이 처음 나온 블록 자리에 table 블록 하나
    tables = _tables(bp, lines_all)
    taken: dict[int, int] = {}            # id(line) → 표 번호
    for k, (tlines, _blk) in enumerate(tables):
        for ln in tlines:
            taken[id(ln)] = k
    seq: list[dict] = []
    placed = set()
    for b in bp.blocks:
        rest: list[Line] = []
        for ln in b:
            k = taken.get(id(ln))
            if k is None:
                rest.append(ln)
                continue
            if rest:
                seq.extend(_text_block(part, body) for part in _split_by_size(rest))
                rest = []
            if k not in placed:
                placed.add(k)
                seq.append(dict(tables[k][1]))
        if rest:
            seq.extend(_text_block(part, body) for part in _split_by_size(rest))

    # 그림: 처음으로 y가 그림의 y 이상인 블록 앞에 끼운다
    fig_blocks = []
    for f in figs:
        f.words.sort(key=lambda w: (round(w.y0), w.x0))
        f.block = {"type": "figure", "x": _r(f.x0), "y": _r(f.y0), "w": _r(f.x1 - f.x0), "h": _r(f.y1 - f.y0),
                   "src": None, "text": " ".join(w.text for w in f.words), "omitted": None}
        fig_blocks.append(f.block)
    blocks: list[dict] = []
    fi = 0
    for blk in seq:
        while fi < len(fig_blocks) and blk["y"] >= fig_blocks[fi]["y"]:
            blocks.append(fig_blocks[fi])
            fi += 1
        blocks.append(blk)
    blocks.extend(fig_blocks[fi:])
    for i, blk in enumerate(blocks):
        blk["id"] = f"p{number}-b{i}"
        _clamp(blk, bp.w, bp.h)
        for ln in blk.get("lines", ()):
            _clamp(ln, bp.w, bp.h)
    page = {"number": number, "width": _r(bp.w), "height": _r(bp.h),
            "figures_failed": boxes is None, "background_skipped": skipped, "blocks": blocks}
    return page, figs


def _clamp(box: dict, W: float, H: float) -> None:
    """상자를 쪽 안으로 자른다(쪽 밖으로 삐져나간 글자 — 실제 문서에서 봄). 글자는 그대로 둔다."""
    x0, y0 = min(max(box["x"], 0.0), W), min(max(box["y"], 0.0), H)
    x1, y1 = min(max(box["x"] + box["w"], x0), W), min(max(box["y"] + box["h"], y0), H)
    box["x"], box["y"], box["w"], box["h"] = _r(x0), _r(y0), _r(x1 - x0), _r(y1 - y0)


# ── 8. 그림 픽셀 (pdftoppm) ────────────────────────────────────────────

class Budget:
    def __init__(self, deadline: float, max_bytes: int, should_stop=None):
        self.deadline = deadline
        self.max_bytes = max_bytes
        self.used = 0
        self.should_stop = should_stop

    def check_stop(self) -> None:
        """요청한 쪽이 떠났으면(창 닫기·[다른 파일]·새로고침) 남은 쪽·그림을 만들지 않고 멈춘다(4단계 QA)."""
        if self.should_stop is not None and self.should_stop():
            raise LayoutError("CLIENT_GONE", "요청한 쪽이 연결을 끊음")


def _render(data: bytes, page: int, f: Fig, budget: Budget) -> tuple[str | None, str | None]:
    """→ (data URI, None) 또는 (None, 생략 사유)."""
    scale = FIGURE_DPI / 72.0
    x, y = int(round(f.x0 * scale)), int(round(f.y0 * scale))
    w = max(1, int(round((f.x1 - f.x0) * scale)))
    h = max(1, int(round((f.y1 - f.y0) * scale)))
    common = ["-r", str(FIGURE_DPI), "-f", str(page), "-l", str(page),
              "-x", str(x), "-y", str(y), "-W", str(w), "-H", str(h), "-"]   # 출력 이름 없음 → stdout
    out = None
    mime = "png"
    for fmt in (["-png"], ["-jpeg", "-jpegopt", f"quality={FIGURE_JPEG_QUALITY}"]):
        left = budget.deadline - time.monotonic()
        if left <= 0:
            return None, "TIME"
        try:
            proc = _run("pdftoppm", fmt + common, data, timeout=min(pdf_extract.EXTRACT_TIMEOUT_S, left))
        except ExtractError as exc:
            if exc.code == "EXTRACT_TIMEOUT":
                return None, ("TIME" if time.monotonic() >= budget.deadline else "FAILED")
            return None, "FAILED"
        if proc.returncode != 0 or not proc.stdout:
            return None, "FAILED"
        out = proc.stdout
        mime = "png" if fmt[0] == "-png" else "jpeg"
        if mime == "png" and len(out) > FIGURE_PNG_MAX_BYTES:
            continue
        break
    if out is None:
        return None, "FAILED"
    if budget.used + len(out) > budget.max_bytes:
        return None, "LIMIT"
    budget.used += len(out)
    return f"data:image/{mime};base64," + base64.b64encode(out).decode("ascii"), None


def _render_page(data: bytes, page: dict, figs: list[Fig], budget: Budget) -> tuple[int, int]:
    total = omitted = 0
    for k, f in enumerate(figs):
        total += 1
        budget.check_stop()
        if k >= FIGURES_PER_PAGE:
            reason = "LIMIT"
        elif time.monotonic() >= budget.deadline:
            reason = "TIME"
        elif budget.used >= budget.max_bytes:
            reason = "LIMIT"
        else:
            src, reason = _render(data, page["number"], f, budget)
            f.block["src"] = src
        if reason:
            f.block["src"] = None
            f.block["omitted"] = reason
            omitted += 1
    return total, omitted


# ── 공개 함수 ──────────────────────────────────────────────────────────

def _range(first, last, pages: int) -> tuple[int, int]:
    def is_int(v):
        return isinstance(v, int) and not isinstance(v, bool)
    if first is None:
        first = 1
    if not is_int(first) or (last is not None and not is_int(last)):
        raise LayoutError("BAD_RANGE", "쪽 번호가 정수가 아님")
    if first < 1 or first > pages:
        raise LayoutError("BAD_RANGE", f"from={first} 범위 밖(1~{pages})")
    if last is None:
        last = first + LAYOUT_PAGES_PER_REQ - 1
    if last < first:
        raise LayoutError("BAD_RANGE", "to < from")
    return first, min(last, pages, first + LAYOUT_PAGES_PER_REQ - 1)


def _valid_body(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if BODY_SIZE_MIN <= v <= BODY_SIZE_MAX else None


def _pages(data: bytes, bpages: list[BPage], first: int, fonts: Fonts, body: float,
           budget: Budget) -> tuple[list[dict], int, int]:
    """bbox 쪽(first부터) → 쪽 dict 목록. 그림 위치·픽셀은 LAYOUT_PAGES_PER_REQ쪽씩."""
    out: list[dict] = []
    figures = omitted = 0
    last = first + len(bpages) - 1
    for start in range(first, last + 1, LAYOUT_PAGES_PER_REQ):
        end = min(last, start + LAYOUT_PAGES_PER_REQ - 1)
        budget.check_stop()
        images = _get_images(data, start, end, budget.deadline)
        for n in range(start, end + 1):
            budget.check_stop()
            page, figs = _build_page(n, bpages[n - first], fonts.pieces.get(n, []), images.get(n), body)
            t, o = _render_page(data, page, figs, budget)
            figures += t
            omitted += o
            out.append(page)
    return out, figures, omitted


def layout(data: bytes, first: int = 1, last: int | None = None,
           body_size: float | None = None, deadline: float | None = None, should_stop=None) -> dict:
    """PDF 바이트 → SPEC §10.4의 dict. first·last 범위 정리(§10.3)는 이 함수가 한다."""
    try:
        return _layout(data, first, last, body_size, deadline, should_stop)
    except ExtractError:
        raise
    except Exception as exc:  # 계약: 예상 못 한 것은 BROKEN_PDF로 감싼다
        raise ExtractError("BROKEN_PDF", f"예상 못 한 예외 {type(exc).__name__}") from None


def _layout(data, first, last, body_size, deadline, should_stop=None) -> dict:
    if deadline is None:
        deadline = time.monotonic() + LAYOUT_BUDGET_S
    _check_input(data)
    pages, title, page_sizes = _info(data)
    first, last = _range(first, last, pages)

    body = _valid_body(body_size)
    if body is None:
        fonts = _get_fonts(data, None, None)      # 문서 전체: 본문 크기 + (같은 출력의) 이 범위 쪽 꾸밈 + 책갈피
        body = _body_size(fonts)
    else:
        fonts = _get_fonts(data, first, last)     # [실측] 범위로 돌려도 책갈피는 문서 전체가 나온다
    bpages = _parse_bbox(_run_ok("pdftotext", ["-enc", "UTF-8", "-bbox-layout", "-f", str(first), "-l", str(last),
                                               "-", "-"], data))
    bpages = _fit_bpages(bpages, page_sizes, first, last)
    budget = Budget(deadline, LAYOUT_FIGURE_BYTES, should_stop)
    out_pages, figures, omitted = _pages(data, bpages, first, fonts, body, budget)
    outline = _outline(fonts, pages)
    return {
        "doc": {"pages": pages, "title": title, "page_sizes": page_sizes, "outline": outline,
                "outline_source": "bookmarks" if outline else "none"},
        "from": first,
        "to": last,
        "body_size": _r(body),
        "pages": out_pages,
        "notes": {"figures": figures, "figures_omitted": omitted},
    }


def _fit_bpages(bpages: list[BPage], page_sizes: list[list[float]], first: int, last: int) -> list[BPage]:
    """pdftotext가 낸 쪽 수가 범위와 다르면(이상한 PDF) 빈 쪽으로 채우거나 자른다.

    쪽 크기는 pdfinfo(회전 반영) 값으로 바꾼다. [실측 2026-11-18] /Rotate 90 쪽에서 -bbox-layout의 단어 좌표는
    회전한 화면 좌표인데 <page width height>만 회전 전 값(612×792)을 낸다 — pdftohtml·pdftocairo는 회전 후(792×612).
    """
    want = last - first + 1
    bpages = bpages[:want]
    while len(bpages) < want:
        w, h = page_sizes[first + len(bpages) - 1]
        bpages.append(BPage(w, h))
    for i, bp in enumerate(bpages):
        bp.w, bp.h = page_sizes[first + i - 1]
    return bpages


def export_html(data: bytes, file_name: str = "", deadline: float | None = None,
                should_stop=None) -> tuple[str, dict]:
    """PDF 바이트 → (내려받을 HTML 문자열, {"pages", "figures", "figures_omitted"}). 문서 전체, SPEC §10.6."""
    try:
        return _export(data, file_name, deadline, should_stop)
    except ExtractError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ExtractError("BROKEN_PDF", f"예상 못 한 예외 {type(exc).__name__}") from None


def _export(data: bytes, file_name: str, deadline: float | None, should_stop=None) -> tuple[str, dict]:
    if deadline is None:
        deadline = time.monotonic() + EXPORT_BUDGET_S
    _check_input(data)
    pages, title, page_sizes = _info(data)
    fonts = _get_fonts(data, None, None)
    body = _body_size(fonts)
    bpages = _parse_bbox(_run_ok("pdftotext", ["-enc", "UTF-8", "-bbox-layout", "-", "-"], data))
    bpages = _fit_bpages(bpages, page_sizes, 1, pages)
    budget = Budget(deadline, EXPORT_FIGURE_BYTES, should_stop)
    out_pages, figures, omitted = _pages(data, bpages, 1, fonts, body, budget)
    outline = _outline(fonts, pages)
    stats = {"pages": pages, "figures": figures, "figures_omitted": omitted}
    return _html_doc(title, file_name, pages, outline, out_pages, stats), stats


def export_file_name(file_name: str) -> str:
    """SPEC §10.6 파일 이름 규칙. 끝의 .pdf(대소문자 무시) 빼기 → 금지 문자·제어문자를 _로 → 앞뒤 공백·마침표 제거
    → 100자 → "{이름}_추출.html". 비면 "추출한_텍스트.html"."""
    name = file_name or ""
    if name.lower().endswith(".pdf"):
        name = name[:-4]
    name = _FNAME_BAD_RE.sub("_", name)
    name = _EDGE_RE.sub("", name)
    name = name[:100]
    return f"{name}_추출.html" if name else "추출한_텍스트.html"


# ── 10.6 HTML ──────────────────────────────────────────────────────────

_CSS = """
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;padding:24px 16px 48px;font:16px/1.7 system-ui,-apple-system,"Segoe UI","Noto Sans KR","Malgun Gothic",sans-serif;color:#1f2328;background:#f6f7f9}
header,nav,main{max-width:860px;margin:0 auto}
header h1{font-size:1.6rem;line-height:1.3;margin:0 0 .4rem}
.src{color:#57606a;font-size:.9rem;margin:0 0 .3rem}
.note{color:#57606a;font-size:.85rem;margin:.2rem 0}
.warn{color:#9a3412}
nav{background:#fff;border:1px solid #d0d7de;border-radius:8px;padding:12px 20px;margin-top:16px}
nav h2{font-size:1.05rem;margin:0 0 .4rem}
nav ol{margin:0;padding-left:1.3rem}
nav a{color:#0b57d0}
.page{background:#fff;border:1px solid #d0d7de;border-radius:8px;padding:24px 28px;margin-top:20px;overflow-wrap:anywhere}
.page-no{color:#57606a;font-size:.8rem;margin:0 0 .6rem;border-bottom:1px solid #eaeef2;padding-bottom:.3rem}
.page h2{font-size:1.45rem;line-height:1.35;margin:1.1rem 0 .5rem}
.page h3{font-size:1.2rem;line-height:1.4;margin:1rem 0 .4rem}
.page h4{font-size:1.05rem;margin:.9rem 0 .3rem}
.page p{margin:.5rem 0}
table{border-collapse:collapse;margin:.8rem 0;max-width:100%;display:block;overflow-x:auto}
th,td{border:1px solid #8c959f;padding:4px 10px;text-align:left;vertical-align:top}
th{background:#f0f3f6}
figure{margin:1rem 0}
figure img{max-width:100%;height:auto;display:block}
figcaption,.figtext{color:#57606a;font-size:.85rem;margin-top:.3rem}
.omitted{color:#9a3412;font-size:.9rem;border:1px dashed #d4a373;padding:6px 10px;border-radius:6px}
@media print{
  body{background:#fff;padding:0;font-size:11pt}
  .page{border:0;border-radius:0;padding:0;margin:0;break-before:page}
  nav{border:0;padding:0}
  a{color:inherit;text-decoration:none}
  figure,table,tr{break-inside:avoid}
}
""".strip()

_OMIT_TEXT = {"LIMIT": "파일 크기 제한", "TIME": "시간 제한", "FAILED": "불러오지 못함"}


def _e(s: str) -> str:
    return html.escape(s, quote=True)


def _toc_items(outline: list[dict], pages: list[dict]) -> list[tuple[int, str, str]]:
    """(level, 제목, href). 책갈피가 있으면 책갈피(#p쪽), 없으면 h1·h2 블록(#p쪽-b번호)."""
    if outline:
        return [(it["level"], it["title"], f"#p{it['page']}") for it in outline]
    items = []
    for p in pages:
        for b in p["blocks"]:
            if b["type"] == "text" and b["role"] in ("h1", "h2") and len(items) < OUTLINE_MAX:
                items.append((1 if b["role"] == "h1" else 2, b["text"][:TITLE_MAX], f"#{b['id']}"))
    return items


def _toc_html(items: list[tuple[int, str, str]]) -> str:
    if not items:
        return ""
    out = ['<nav aria-label="목차"><h2>목차</h2><ol>']
    cur = 1
    first = True
    for level, title, href in items:
        level = max(1, min(level, cur + 1))     # 두 단계 이상 깊어지면 한 단계만
        if level == cur + 1 and not first:
            out.append("<ol>")
        elif level < cur:
            out.append("</li>" + "</ol></li>" * (cur - level))
        elif not first:
            out.append("</li>")
        if first:
            level = 1
        cur = level
        first = False
        out.append(f'<li><a href="{_e(href)}">{_e(title)}</a>')
    out.append("</li>" + "</ol></li>" * (cur - 1) + "</ol></nav>")
    return "".join(out)


def _block_html(b: dict, page_no: int, fig_no: int) -> str:
    bid = _e(b["id"])
    if b["type"] == "text":
        tag = {"h1": "h2", "h2": "h3", "h3": "h4"}.get(b["role"], "p")
        return f'<{tag} id="{bid}">{_e(b["text"])}</{tag}>'
    if b["type"] == "table":
        rows = b["rows"]
        head = bool(rows) and all(c["bold"] for c in rows[0] if c["text"]) and any(c["text"] for c in rows[0])
        parts = [f'<table id="{bid}"><tbody>']
        for i, row in enumerate(rows):
            tag = "th" if head and i == 0 else "td"
            parts.append("<tr>" + "".join(f"<{tag}>{_e(c['text'])}</{tag}>" for c in row) + "</tr>")
        parts.append("</tbody></table>")
        return "".join(parts)
    # figure
    src = b.get("src")
    cap = b.get("text") or ""
    if isinstance(src, str) and _DATA_URI_RE.match(src):
        w = max(1, round(b["w"] * 96 / 72))
        h = max(1, round(b["h"] * 96 / 72))
        caption = f"<figcaption>{_e(cap)}</figcaption>" if cap else ""
        return (f'<figure id="{bid}"><img src="{src}" alt="{page_no}쪽 그림 {fig_no}" '
                f'width="{w}" height="{h}">{caption}</figure>')
    reason = _OMIT_TEXT.get(b.get("omitted") or "FAILED", "불러오지 못함")
    extra = f'<p class="figtext">{_e(cap)}</p>' if cap else ""
    return f'<p class="omitted" id="{bid}">[그림 생략: {reason}]</p>{extra}'


def _html_doc(title: str, file_name: str, pages: int, outline: list[dict], out_pages: list[dict],
              stats: dict) -> str:
    stem = file_name[:-4] if file_name.lower().endswith(".pdf") else file_name
    doc_title = title or stem.strip() or "제목 없는 문서"
    when = time.strftime("%Y-%m-%d %H:%M")
    src_name = file_name or "이름 없는 파일"
    head = [
        "<!doctype html>",
        '<html lang="ko">',
        "<head>",
        '<meta charset="utf-8">',
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src data:; style-src 'unsafe-inline'\">",
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="generator" content="pdf-summary/1">',
        f"<title>{_e(doc_title)} — 추출한 텍스트</title>",
        f"<style>\n{_CSS}\n</style>",
        "</head>",
        "<body>",
        "<header>",
        f"<h1>{_e(doc_title)}</h1>",
        f'<p class="src">원본: {_e(src_name)} · {pages}쪽 · {when} 한눈 PDF 요약에서 추출</p>',
        '<p class="note">PDF에서 글자·표·그림을 뽑아 다시 배치한 문서입니다. 표와 제목은 추정이라 원본과 다를 수 있습니다.</p>',
    ]
    if stats["figures_omitted"]:
        head.append(f'<p class="note warn">그림 {stats["figures_omitted"]}개는 파일 크기·시간 제한으로 넣지 않았습니다.</p>')
    head.append("</header>")
    toc = _toc_html(_toc_items(outline, out_pages))
    if toc:
        head.append(toc)
    body = ["<main>"]
    for p in out_pages:
        n = p["number"]
        body.append(f'<section class="page" id="p{n}" aria-label="{n}쪽">')
        body.append(f'<p class="page-no">{n}쪽</p>')
        fig_no = 0
        for b in p["blocks"]:
            if b["type"] == "figure":
                fig_no += 1
            body.append(_block_html(b, n, fig_no))
        if p["figures_failed"]:
            body.append('<p class="omitted">이 쪽의 그림은 불러오지 못했습니다.</p>')
        body.append("</section>")
    body.append("</main>")
    body.append("</body>")
    body.append("</html>")
    return "\n".join(head + body) + "\n"
