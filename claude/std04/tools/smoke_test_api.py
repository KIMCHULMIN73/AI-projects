#!/usr/bin/env python3
"""OpenRouter API가 실제로 도는지 확인하는 연기 테스트. 앱은 쓰지 않는다(개발 전용).

    python3 tools/smoke_test_api.py                          # Models 파일에 적힌 모델로
    python3 tools/smoke_test_api.py --text-model z-ai/glm-5.2:free
    python3 tools/smoke_test_api.py --only image

**답을 미리 아는 질문만 던진다.** "잘 대답하네"로는 API가 도는지 알 수 없고,
모델이 지어냈는지도 구별할 수 없기 때문이다. 이미지 테스트는 스크립트가 직접
그린 그림을 보내고 **거기 적어 둔 코드값을 읽어 오는지**로 판정한다.

이미지 생성에만 Pillow를 쓴다 — 개발 전용이고, 앱의 의존성이 아니다.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

API = "https://openrouter.ai/api/v1/chat/completions"
MODELS_FILE = Path(config.__file__).resolve().parent / "Models"

# 이미지에 심어 둘 값. 모델이 그림을 실제로 봤는지 가르는 기준이다.
SECRET_CODE = "STD04-7391"
SHAPES = ("빨간 원", "파란 네모", "초록 삼각형")


def read_models_file() -> dict[str, str]:
    """`Models`에 적힌 모델 ID를 읽는다.

    이 파일이 "어떤 모델을 쓰기로 했는가"의 유일한 기록이므로 여기서 읽는다.
    (git 제외 대상이라 클론한 사람에게는 없을 수 있다 — 그때는 옵션으로 넘긴다.)

    형식이 두 번 바뀐 전례가 있어 둘 다 받는다:

        [Image API model]                  |   image model : dots-studio/...
        dots-studio/dots-3-note-preview:free

    판별은 줄에 "image"/"text"가 있는지로 하고, 모델 ID는 `제공자/모델` 꼴
    (슬래시가 있는 줄)로 찾는다. 형식이 또 바뀌어도 대개 살아남는다.
    """
    out: dict[str, str] = {}
    if not MODELS_FILE.is_file():
        return out
    kind = None
    for raw in MODELS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip().strip("[]")
        if not line:
            continue
        lowered = line.lower()
        # 같은 줄에 라벨과 값이 함께 있는 옛 형식: "image model : provider/id"
        label, sep, tail = line.partition(":")
        if sep and "/" in tail and ("image" in label.lower() or "text" in label.lower()):
            out["image" if "image" in label.lower() else "text"] = tail.strip()
            continue
        if "/" in line:                      # 모델 ID 줄
            if kind:
                out[kind] = line
                kind = None
        elif "image" in lowered:             # 구획 머리말
            kind = "image"
        elif "text" in lowered:
            kind = "text"
    return out


def make_test_image() -> bytes:
    """도형 3개와 코드값이 들어간 PNG를 만든다."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (520, 320), "white")
    d = ImageDraw.Draw(img)
    d.ellipse((40, 40, 160, 160), fill="#d62828")            # 빨간 원
    d.rectangle((200, 40, 320, 160), fill="#1d4ed8")          # 파란 네모
    d.polygon([(420, 40), (490, 160), (350, 160)], fill="#16a34a")  # 초록 삼각형
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 44)
    d.text((40, 220), SECRET_CODE, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def ask(model: str, content, *, max_tokens: int = 1500) -> tuple[dict, float]:
    """한 번 호출하고 `(응답 JSON, 걸린 초)`를 돌려준다."""
    body = json.dumps(
        {"model": model, "messages": [{"role": "user", "content": content}], "max_tokens": max_tokens}
    ).encode()
    req = urllib.request.Request(
        API,
        data=body,
        headers={**config.auth_header(), "X-Title": "std04 smoke test"},
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp), time.time() - started


def text_of(payload: dict) -> tuple[str, bool]:
    """`(보여줄 답, 추론중_잘림)`.

    추론형 모델은 생각을 `reasoning`에, 최종 답을 `content`에 나눠 담는다.
    max_tokens가 모자라면 content가 빈 채로 끝나므로 그 경우를 구분해 알린다.
    """
    msg = payload["choices"][0]["message"]
    content = (msg.get("content") or "").strip()
    if content:
        return content, False
    return (msg.get("reasoning") or "").strip(), True


def run(label: str, model: str, content, judge) -> bool:
    print(f"\n── {label} · {model}")
    try:
        payload, secs = ask(model, content)
    except urllib.error.HTTPError as exc:
        detail = config.redact(exc.read().decode(errors="replace"))[:300]
        print(f"  [FAIL] HTTP {exc.code} — {detail}")
        return False
    except Exception as exc:
        print(f"  [FAIL] {type(exc).__name__}: {config.redact(exc)}")
        return False

    answer, truncated = text_of(payload)
    usage = payload.get("usage", {})
    finish = payload["choices"][0].get("finish_reason")
    print(f"  응답({secs:.1f}초, 토큰 {usage.get('prompt_tokens')}→{usage.get('completion_tokens')}, "
          f"finish={finish}):")
    if truncated:
        print("    (최종 답변이 비어 reasoning을 대신 표시 — max_tokens 부족)")
    for line in (answer or "(빈 응답)").splitlines()[:12]:
        print(f"    {line}")
    ok, why = judge(answer)
    print(f"  [{'PASS' if ok else 'FAIL'}] {why}")
    return ok


def main() -> int:
    declared = read_models_file()
    p = argparse.ArgumentParser()
    p.add_argument("--text-model", default=declared.get("text"))
    p.add_argument("--image-model", default=declared.get("image"))
    p.add_argument("--only", choices=["text", "image"])
    args = p.parse_args()

    results = []

    if args.only != "image":
        if not args.text_model:
            print("[SKIP] 텍스트 모델이 정해지지 않았습니다 "
                  "(Models 파일의 'text model :' 이 비어 있음). --text-model 로 지정하세요.")
        else:
            prompt = (
                "다음 두 가지에만 답하세요. 설명을 붙이지 마세요.\n"
                "1) 대한민국의 수도는? (한 단어)\n"
                "2) 17 곱하기 23 은?"
            )
            results.append(
                run("텍스트 생성", args.text_model, prompt,
                    lambda a: (("서울" in a or "Seoul" in a) and "391" in a,
                               "'서울'과 391이 모두 나왔다" if ("서울" in a or "Seoul" in a) and "391" in a
                               else "기대한 답(서울 / 391)이 나오지 않았다"))
            )

    if args.only != "text":
        if not args.image_model:
            print("[SKIP] 이미지 모델이 정해지지 않았습니다.")
        else:
            png = make_test_image()
            print(f"\n테스트 이미지 생성: 520x320 PNG, {len(png):,} bytes "
                  f"(도형 3개 + 코드 {SECRET_CODE})")
            content = [
                {"type": "text",
                 "text": "이 이미지에 대해 두 가지만 답하세요.\n"
                         "1) 적혀 있는 코드 문자열을 그대로\n"
                         "2) 보이는 도형들의 색과 모양"},
                {"type": "image_url",
                 "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
            ]
            def judge(a: str):
                code_ok = SECRET_CODE in a.replace(" ", "")
                colors = sum(c in a for c in ("빨", "red", "Red", "파", "blue", "Blue", "초록", "green", "Green"))
                return (code_ok and colors >= 2,
                        f"코드 판독 {'성공' if code_ok else '실패'}, 색상 언급 {colors}종")
            results.append(run("이미지 인식", args.image_model, content, judge))

    print("\n" + "=" * 50)
    if not results:
        print("실행한 테스트가 없습니다.")
        return 1
    print(f"{sum(results)}/{len(results)} 통과")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
