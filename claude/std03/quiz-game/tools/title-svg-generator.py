#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""제목 글자를 '리벳 구멍 뚫린 금속 조각' 모양의 SVG로 조립해 주는 개발용 생성기.

std02 "new version"의 tools/title-svg-generator.py를 이식한 것이다.
원본과 원리는 같고, 이 프로젝트의 제목에 필요한 자모(ㅈ ㅋ ㅜ ㅔ)와 음절,
그리고 복합모음(ㅘ ㅟ) 조합 규칙(E 규칙)이 추가됐다.

⚠ 이 파일은 앱의 일부가 아니다. index.html/css/js 어디에서도 로드하지 않으며,
   이 파일을 지워도 게임은 그대로 동작한다.
   제목 문구를 바꿀 때만 개발자가 수동으로 한 번 돌리는 도구다.

사용법:
    python3 tools/title-svg-generator.py            # SVG를 화면에 출력
    python3 tools/title-svg-generator.py --inject   # index.html의 마커 사이를 교체

    문구를 바꾸려면 맨 아래 LINES/LABEL을 수정한 뒤 --inject로 다시 생성한다.
    (SYL/JAMO에 없는 글자를 쓰면 KeyError가 난다 — 해당 음절의 조합 규칙을 추가할 것)

원리:
    - 자모를 0~100 로컬 좌표계의 폴리라인/타원으로 정의한다.
    - 음절은 한글 조합 규칙(A~E)에 따라 자모를 배치 박스에 앉혀 만든다.
    - 좌표는 파이썬에서 절대좌표로 미리 변환하므로 획 두께가 어디서나 일정하다.
    - 리벳 구멍은 <mask>의 검은 원으로 '뚫는다'. 흰 원을 덧그리는 게 아니라 실제로
      뚫려야 뒤의 널판지 나뭇결이 비쳐 보인다.
"""

import math
import os
import re
import sys

# ── 형태 파라미터 ──────────────────────────────────────
W = 10.0        # 획(금속 조각) 두께
R_HOLE = 3.0    # 리벳 구멍 반지름
INSET = 9.0     # 획 끝에서 구멍 중심까지의 거리
MIN_GAP = 15.0  # 구멍끼리 이보다 가까우면 하나로 합친다
CELL = 100.0    # 음절 한 칸
GAP = 16.0      # 음절 간격
WORDGAP = 46.0  # 띄어쓰기 간격
LINEGAP = 34.0  # 줄 간격
PAD = 12.0      # 바깥 여백

# ── 자모: 로컬 0~100 좌표계의 폴리라인('p')과 타원('e') ──
JAMO = {
    'ㄱ': {'p': [[(12, 18), (84, 18), (76, 88)]]},
    'ㄴ': {'p': [[(20, 12), (20, 82), (88, 82)]]},
    'ㄷ': {'p': [[(86, 12), (14, 12), (14, 88), (86, 88)]]},
    'ㄹ': {'p': [[(12, 10), (86, 10), (86, 50)],
                 [(86, 50), (14, 50), (14, 90), (88, 90)]]},
    'ㅁ': {'p': [[(16, 14), (84, 14), (84, 86), (16, 86), (16, 14)]]},
    'ㅅ': {'p': [[(50, 10), (10, 90)], [(50, 10), (90, 90)]]},
    'ㅈ': {'p': [[(10, 20), (90, 20)], [(50, 20), (12, 92)], [(50, 20), (88, 92)]]},
    'ㅊ': {'p': [[(50, 0), (50, 12)], [(10, 28), (90, 28)],
                 [(52, 28), (12, 92)], [(52, 28), (90, 92)]]},
    'ㅋ': {'p': [[(12, 16), (84, 16), (76, 88)], [(30, 52), (80, 49)]]},
    'ㅎ': {'p': [[(50, 0), (50, 8)], [(14, 20), (86, 20)]],
           'e': [(50, 64, 32, 26)]},
    'ㅇ': {'e': [(50, 50, 38, 36)]},
    'ㅣ': {'p': [[(50, 6), (50, 94)]]},
    'ㅏ': {'p': [[(40, 6), (40, 94)], [(40, 50), (86, 50)]]},
    'ㅓ': {'p': [[(64, 6), (64, 94)], [(16, 50), (64, 50)]]},
    'ㅔ': {'p': [[(46, 6), (46, 94)], [(86, 6), (86, 94)], [(12, 50), (46, 50)]]},
    'ㅡ': {'p': [[(6, 50), (94, 50)]]},
    'ㅗ': {'p': [[(6, 78), (94, 78)], [(50, 24), (50, 78)]]},
    'ㅜ': {'p': [[(6, 28), (94, 28)], [(50, 28), (50, 82)]]},
}


# ── 음절 조합 규칙: (자모, 배치박스 x, y, w, h) ──────────
def A(cho, jung):            # 초성 + 세로모음
    return [(cho, 2, 8, 48, 84), (jung, 52, 4, 46, 92)]


def B(cho, jung, jong):      # 초성 + 세로모음 + 받침
    return [(cho, 2, 2, 44, 50), (jung, 50, 0, 48, 58), (jong, 6, 56, 88, 44)]


def C(cho, jung):            # 초성 + 가로모음
    return [(cho, 24, 0, 52, 48), (jung, 0, 48, 100, 50)]


def D(cho, jung, jong):      # 초성 + 가로모음 + 받침
    return [(cho, 26, 0, 48, 34), (jung, 0, 32, 100, 28), (jong, 8, 58, 84, 42)]


def E(cho, jung_h, jung_v):  # 초성 + 복합모음(가로+세로): ㅘ ㅟ ㅢ 등
    # 초성과 가로모음이 왼쪽에 위아래로 쌓이고, 세로모음이 오른쪽 전체 높이를 쓴다.
    return [(cho, 0, 4, 48, 48), (jung_h, 0, 48, 54, 48), (jung_v, 54, 2, 46, 96)]


SYL = {
    '누': C('ㄴ', 'ㅜ'),
    '가': A('ㄱ', 'ㅏ'),
    '잘': B('ㅈ', 'ㅏ', 'ㄹ'),
    '하': A('ㅎ', 'ㅏ'),
    '나': A('ㄴ', 'ㅏ'),
    '좌': E('ㅈ', 'ㅗ', 'ㅏ'),
    # D 규칙의 받침 박스(폭 84)는 ㅇ에는 너무 넓어 납작해진다 → 받침만 좁혀 원형에 가깝게.
    '충': [('ㅊ', 26, 0, 48, 34), ('ㅜ', 0, 32, 100, 28), ('ㅇ', 28, 58, 44, 42)],
    '우': C('ㅇ', 'ㅜ'),
    '돌': D('ㄷ', 'ㅗ', 'ㄹ'),
    '퀴': E('ㅋ', 'ㅜ', 'ㅣ'),
    '즈': C('ㅈ', 'ㅡ'),
    '게': A('ㄱ', 'ㅔ'),
    '임': B('ㅇ', 'ㅣ', 'ㅁ'),
}

# 손으로 조각을 붙인 느낌을 내려고 음절마다 아주 살짝 기울인다(도 단위).
TILT = {'누': -1.4, '가': 1.2, '잘': -1.0, '하': 1.3, '나': -1.2,
        '좌': 1.1, '충': -1.5, '우': 1.4, '돌': -0.9, '퀴': 1.2,
        '즈': -1.3, '게': 1.0, '임': -1.1}


def along(p, q, dist):
    """p에서 q 방향으로 dist만큼 떨어진 점."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    length = math.hypot(dx, dy) or 1.0
    return (p[0] + dx / length * dist, p[1] + dy / length * dist)


def holes_for_polyline(pts):
    """획 양 끝은 안쪽으로 INSET만큼, 꺾이는 지점에는 그대로 구멍을 둔다.
       너무 짧은 획은 가운데 하나만 뚫는다."""
    total = sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
    if total < 2 * INSET + MIN_GAP:
        return [((pts[0][0] + pts[-1][0]) / 2, (pts[0][1] + pts[-1][1]) / 2)]

    return [along(pts[0], pts[1], INSET),
            along(pts[-1], pts[-2], INSET)] + list(pts[1:-1])


def dedupe(points):
    """구멍이 겹쳐 획이 끊어져 보이지 않도록 가까운 것들을 솎아낸다."""
    kept = []
    for p in points:
        if all(math.dist(p, k) >= MIN_GAP for k in kept):
            kept.append(p)
    return kept


def line_width(text):
    width = 0.0
    for ch in text:
        width += WORDGAP if ch == ' ' else CELL + GAP
    return width - GAP


def build_syllable(ch, ox, oy):
    """한 음절의 획과 구멍을 절대좌표로 만든다."""
    def place(pt, box):
        x, y, w, h = box
        return (x + pt[0] * w / 100.0, y + pt[1] * h / 100.0)

    strokes, holes = [], []
    for jamo, bx, by, bw, bh in SYL[ch]:
        box = (ox + bx, oy + by, bw, bh)
        shape = JAMO[jamo]

        for poly in shape.get('p', []):
            pts = [place(p, box) for p in poly]
            strokes.append(('poly', pts))
            holes += holes_for_polyline(pts)

        for (ex, ey, rx, ry) in shape.get('e', []):
            center = place((ex, ey), box)
            radii = (rx * box[2] / 100.0, ry * box[3] / 100.0)
            strokes.append(('ellipse', (center, radii)))
            for angle in (95, 215, 335):   # 원형 자모에는 리벳 3개
                t = math.radians(angle)
                holes.append((center[0] + radii[0] * math.cos(t),
                              center[1] + radii[1] * math.sin(t)))

    return strokes, dedupe(holes)


def build(lines):
    """각 줄을 가운데 정렬해 음절 그룹을 배치한다."""
    total_w = max(line_width(t) for t in lines) + PAD * 2
    total_h = len(lines) * CELL + (len(lines) - 1) * LINEGAP + PAD * 2

    groups = []
    for index, text in enumerate(lines):
        oy = PAD + index * (CELL + LINEGAP)
        ox = (total_w - line_width(text)) / 2.0
        for ch in text:
            if ch == ' ':
                ox += WORDGAP
                continue
            strokes, holes = build_syllable(ch, ox, oy)
            groups.append((ch, ox + CELL / 2, oy + CELL / 2, strokes, holes))
            ox += CELL + GAP

    return groups, total_w, total_h


def to_svg(lines, label, indent='        '):   # index.html의 <h1> 안쪽 들여쓰기
    groups, w, h = build(lines)
    out = []
    add = lambda s: out.append(indent + s)

    add(f'<svg class="title-art" viewBox="0 0 {w:.0f} {h:.0f}" role="img" aria-label="{label}">')
    add(f'  <title>{label}</title>')
    add('  <defs>')
    add(f'    <mask id="titleRivets" maskUnits="userSpaceOnUse" x="0" y="0" width="{w:.0f}" height="{h:.0f}">')
    add(f'      <rect x="0" y="0" width="{w:.0f}" height="{h:.0f}" fill="#fff"/>')
    for _, _, _, _, holes in groups:
        for (hx, hy) in holes:
            add(f'      <circle cx="{hx:.1f}" cy="{hy:.1f}" r="{R_HOLE:g}"/>')
    add('    </mask>')
    add('  </defs>')
    add(f'  <g mask="url(#titleRivets)" fill="none" stroke="currentColor" stroke-width="{W:g}"'
        ' stroke-linecap="round" stroke-linejoin="round">')
    for ch, cx, cy, strokes, _ in groups:
        add(f'    <g transform="rotate({TILT.get(ch, 0)} {cx:.1f} {cy:.1f})">')
        for kind, data in strokes:
            if kind == 'poly':
                pts = ' '.join(f'{p[0]:.1f},{p[1]:.1f}' for p in data)
                add(f'      <polyline points="{pts}"/>')
            else:
                center, radii = data
                add(f'      <ellipse cx="{center[0]:.1f}" cy="{center[1]:.1f}"'
                    f' rx="{radii[0]:.1f}" ry="{radii[1]:.1f}"/>')
        add('    </g>')
    add('  </g>')
    add('</svg>')
    return '\n'.join(out)


START = '<!-- TITLE-ART:START (tools/title-svg-generator.py 로 생성 — 직접 고치지 말 것) -->'
END = '<!-- TITLE-ART:END -->'


def inject(svg):
    """index.html의 마커 사이를 새로 생성한 SVG로 교체한다."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'index.html')
    html = open(path, encoding='utf-8').read()

    if START not in html or END not in html:
        sys.exit('index.html에서 TITLE-ART 마커를 찾지 못했습니다.')

    replaced = re.sub(
        re.escape(START) + r'.*?' + re.escape(END),
        START + '\n' + svg + '\n        ' + END,
        html,
        flags=re.DOTALL,
    )
    open(path, 'w', encoding='utf-8').write(replaced)
    print(f'index.html 갱신 완료: {os.path.normpath(path)}')


LINES = ['누가누가 잘하나', '좌충우돌 퀴즈게임']
LABEL = '누가누가 잘하나 좌충우돌 퀴즈게임'

if __name__ == '__main__':
    markup = to_svg(LINES, LABEL)
    if '--inject' in sys.argv:
        inject(markup)
    else:
        print(markup)
