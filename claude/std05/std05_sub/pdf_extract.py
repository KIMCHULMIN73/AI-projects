"""PDF 바이트 → 본문·문서 정보. 시스템 도구 `pdfinfo`·`pdftotext`(poppler-utils)를 쓴다.

    import pdf_extract
    info = pdf_extract.extract(pdf_bytes)   # 실패하면 pdf_extract.ExtractError(code, log)

- 파이썬 PDF 패키지 대신 poppler를 쓰는 이유는 PRD D3. 없으면 `TOOL_MISSING`.
- **업로드 내용을 디스크에 쓰지 않는다.** 바이트를 도구의 stdin으로 넘기고 stdout으로 받는다(임시 파일 없음).
- 하위 프로세스는 `shell=True` 없이 고정 인자 목록으로만 실행한다. 사용자 입력(파일 이름 등)은 인자로 넘기지 않는다.
- 도구 한 번 실행마다 `EXTRACT_TIMEOUT_S` 제한. 넘으면 프로세스를 죽이고 `EXTRACT_TIMEOUT`.
- `ExtractError.log`에는 문서 본문·제목·파일 이름을 넣지 않는다(서버가 로그로 찍는다).

계약: SPEC.md §5.1. 수치: SPEC.md §3.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata

MAX_PAGES: int = 300
MAX_INPUT_CHARS: int = 60_000   # llm.py의 같은 이름 상수와 같은 값이어야 한다
MIN_TEXT_CHARS: int = 20        # 공백 제외 글자 수. 이보다 적으면 스캔본으로 보고 NO_TEXT
EXTRACT_TIMEOUT_S: float = 30   # 도구 한 번 실행당

TOOLS = ("pdftotext", "pdfinfo")
CODES = ("EMPTY_FILE", "NOT_PDF", "ENCRYPTED", "BROKEN_PDF", "NO_TEXT", "TOO_LONG",
         "EXTRACT_TIMEOUT", "TOOL_MISSING")
TITLE_MAX = 200

# \n·\t를 뺀 C0·C1 제어문자와 DEL. (\r도 지운다 — pdftotext는 줄 끝을 \n으로 낸다)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_MANY_BLANK_RE = re.compile(r"\n{4,}")   # 빈 줄 3개 이상(줄바꿈 4개 이상) → 빈 줄 2개


class ExtractError(Exception):
    """추출 실패. `code`는 CODES 중 하나, `log`는 개발자용 짧은 설명(문서 내용 없음)."""

    def __init__(self, code: str, log: str = ""):
        super().__init__(code)
        self.code = code if code in CODES else "BROKEN_PDF"
        self.log = log


def check_tools() -> dict[str, bool]:
    """{"pdftotext": bool, "pdfinfo": bool} — shutil.which로만 확인하고 실행하지 않는다."""
    return {name: shutil.which(name) is not None for name in TOOLS}


def _run(tool: str, args: list[str], data: bytes) -> bytes:
    """도구를 stdin=data로 실행해 stdout을 돌려준다. 실패는 ExtractError로 바꾼다."""
    path = shutil.which(tool)
    if path is None:
        raise ExtractError("TOOL_MISSING", f"{tool} 없음")
    try:
        proc = subprocess.run(
            [path, *args], input=data, capture_output=True,
            timeout=EXTRACT_TIMEOUT_S, check=False,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run이 자식을 죽이고 정리한 뒤 던진다.
        raise ExtractError("EXTRACT_TIMEOUT", f"{tool} {EXTRACT_TIMEOUT_S}s 초과") from None
    except OSError as exc:  # 확인과 실행 사이에 지워졌거나 실행 권한이 없음
        raise ExtractError("TOOL_MISSING", f"{tool} 실행 실패 {type(exc).__name__}") from None
    if proc.returncode != 0:
        # [실측] 열기 암호가 걸리면 종료코드 1 + stderr "Command Line Error: Incorrect password"
        if b"Incorrect password" in proc.stderr:
            raise ExtractError("ENCRYPTED", f"{tool} 종료코드 {proc.returncode}, 암호")
        raise ExtractError("BROKEN_PDF", f"{tool} 종료코드 {proc.returncode}")
    return proc.stdout


def _parse_info(raw: bytes) -> tuple[int, str]:
    """pdfinfo 출력 → (쪽수, 제목). 쪽수를 못 읽으면 BROKEN_PDF."""
    pages: int | None = None
    title = ""
    for line in raw.decode("utf-8", errors="replace").splitlines():
        name, sep, value = line.partition(":")
        if not sep:
            continue
        if name == "Pages":
            try:
                pages = int(value.strip())
            except ValueError:
                pages = None
        elif name == "Title":
            title = value.strip()
    if pages is None or pages < 0:
        raise ExtractError("BROKEN_PDF", "pdfinfo 출력에 Pages 없음")
    title = unicodedata.normalize("NFC", _CONTROL_RE.sub("", title)).strip()[:TITLE_MAX]
    return pages, title


def _clean_page(page: str) -> str:
    page = _CONTROL_RE.sub("", page)
    page = "\n".join(line.rstrip() for line in page.split("\n"))
    page = _MANY_BLANK_RE.sub("\n\n\n", page)
    return page.strip()


def _visible_chars(s: str) -> int:
    return sum(1 for ch in s if not ch.isspace())


def _split_pages(raw: bytes) -> list[str]:
    """pdftotext 출력 → 정규화한 쪽 목록. 쪽 구분은 \\f."""
    text = unicodedata.normalize("NFC", raw.decode("utf-8", errors="replace"))
    pages = text.split("\f")
    # [실측] pdftotext는 마지막 쪽 뒤에도 \f를 붙인다 → 맨 끝의 빈 조각은 쪽이 아니다.
    if len(pages) > 1 and pages[-1].strip() == "":
        pages.pop()
    return [_clean_page(p) for p in pages]


def _fit(pages: list[str]) -> tuple[str, int, int]:
    """쪽 목록 → (본문, 담은 쪽 수, 잘라내기 전 전체 길이).

    쪽 사이는 빈 줄 하나(\\n\\n)로 잇고, 글자가 없는 쪽은 잇지 않는다(빈 줄만 늘어나므로).
    전체가 MAX_INPUT_CHARS를 넘으면 쪽 단위로 앞에서부터 들어가는 데까지 담는다.
    첫 글자 있는 쪽 하나가 이미 넘으면 그 쪽을 MAX_INPUT_CHARS에서 자른다.
    """
    full = "\n\n".join(p for p in pages if p)
    if len(full) <= MAX_INPUT_CHARS:
        return full, len(pages), len(full)

    parts: list[str] = []
    length = 0
    used = 0
    for i, page in enumerate(pages):
        if not page:          # 빈 쪽은 길이를 늘리지 않으므로 담은 것으로 센다
            used = i + 1
            continue
        added = len(page) + (2 if parts else 0)
        if length + added > MAX_INPUT_CHARS:
            if not parts:     # 첫 글자 있는 쪽 하나가 이미 상한을 넘는다
                parts.append(page[:MAX_INPUT_CHARS].rstrip())
                used = i + 1
            break
        parts.append(page)
        length += added
        used = i + 1
    return "\n\n".join(parts), used, len(full)


def extract(data: bytes) -> dict:
    """PDF 바이트 → {text, title, pages, pages_used, empty_pages, chars, chars_total, truncated}.

    디스크에 쓰지 않는다(도구에 stdin으로 넘김). 실패는 모두 ExtractError.
    """
    try:
        return _extract(data)
    except ExtractError:
        raise
    except Exception as exc:  # 계약: 예상 못 한 것은 BROKEN_PDF로 감싼다
        raise ExtractError("BROKEN_PDF", f"예상 못 한 예외 {type(exc).__name__}") from None


def _extract(data: bytes) -> dict:
    if not data:
        raise ExtractError("EMPTY_FILE", "0바이트")
    if b"%PDF-" not in data[:1024]:
        raise ExtractError("NOT_PDF", "앞 1024바이트에 %PDF- 없음")
    missing = [name for name, ok in check_tools().items() if not ok]
    if missing:
        raise ExtractError("TOOL_MISSING", f"{', '.join(missing)} 없음")

    pages, title = _parse_info(_run("pdfinfo", ["-enc", "UTF-8", "-"], data))
    if pages > MAX_PAGES:
        raise ExtractError("TOO_LONG", f"{pages}쪽 > {MAX_PAGES}")

    page_texts = _split_pages(_run("pdftotext", ["-enc", "UTF-8", "-", "-"], data))
    if sum(_visible_chars(p) for p in page_texts) < MIN_TEXT_CHARS:
        empty_all = sum(1 for p in page_texts if _visible_chars(p) == 0)
        raise ExtractError("NO_TEXT", f"공백 제외 글자 {MIN_TEXT_CHARS} 미만, 빈 쪽 {empty_all}/{len(page_texts)}")

    text, used, chars_total = _fit(page_texts)
    truncated = chars_total > MAX_INPUT_CHARS
    # pdftotext의 쪽 수는 pdfinfo와 같아야 한다([실측]). 어긋나더라도 pages_used가 pages를 넘지 않게.
    pages_used = min(used, pages) if truncated else pages
    # 화면은 "글자가 없는 쪽 n개는 빼고 읽었어요"라고 알린다 — 요약에 담은 쪽 안의 빈 쪽만 센다
    # (사용자 결정 2026-11-17, PRD U12 (a)). 잘린 뒤쪽의 빈 쪽은 읽지도 않았으므로 세지 않는다.
    empty_pages = sum(1 for p in page_texts[:pages_used] if _visible_chars(p) == 0)
    return {
        "text": text,
        "title": title,
        "pages": pages,
        "pages_used": pages_used,
        "empty_pages": empty_pages,
        "chars": len(text),
        "chars_total": chars_total,
        "truncated": truncated,
    }
