# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트

**아직 비어 있다 (2026-10-21 기준).** 코드·사양(PRD)·실행 방법이 생기면 이 문서에 프로젝트 개요, 실행/검증 명령, 구조를 채울 것. 형식은 `../std04/CLAUDE.md`를 참고한다.

PRD 작성과 일정 관리는 `.claude/agents/product-manager.md` 서브에이전트가 한다. `PRD.md`(색인·일정표) + `PRD_step<N>.md` + `units/` 구조로 쓰고, 문서만 고치며 앱 코드는 건드리지 않는다. 커밋은 하지 않는다.

## 리포지토리 관례 (형제 프로젝트에서 이어지는 것)

git 루트는 두 단계 위(`../..`, `AI-projects`)이고 원격은 공개 GitHub이다. 형제(`../std01` 숫자 인식기, `../std02` 할 일 관리 앱, `../std03` 퀴즈 게임, `../std04` 냉장고 사진 레시피 추천)와 공유하는 코드·의존성은 없지만 작업 방식은 이어진다:

- **프레임워크·빌드 도구·번들러·npm 없음.** vanilla로 짜고 바로 실행한다. 실행기가 필요하면 `run.sh`를 둔다(std03·std04).
- **외부 의존성 없음.** Python은 표준 라이브러리만 쓴다(`requests`·`python-dotenv`도 쓰지 않는다).
- **자동화 테스트 러너 없음.** 검증은 수동이거나 `tools/` 아래 검증 스크립트로 한다.
- **개발 전용 스크립트는 `tools/`에 두고 앱이 로드하지 않게 한다** — 지워도 앱이 돌아야 한다.
- **사양이 있으면 PRD가 원본이다.** 사양과 어긋나게 구현해야 하면 PRD와 이 문서를 함께 고친다.
- **이 문서를 작업 로그로 계속 갱신한다.** 설계 판단, 사양과 어긋난 지점, 그 이유를 남긴다.
- 반복 작업은 `.claude/commands/`에 슬래시 명령어로, 검토·최적화 같은 역할은 `.claude/agents/`에 서브에이전트로 정리한다(std03·std04).
- API 키는 `.env`(gitignore)에 두고 `.env.example`만 커밋한다(std04의 `config.py` 방식).
- 문서는 한국어로 쓴다.

## 이 환경에서 쓸 수 있는 것 (Raspberry Pi, Linux)

- **Python 3.13.5**, **curl**, **chromium·firefox** — 있다.
- **node / npm / jq — 없다.** JS 문법 검사(`node --check`)는 못 쓰고, JSON은 `python3 -m json.tool`로 다룬다.
- 브라우저 자동화 라이브러리(playwright·selenium)는 없다. 화면 확인을 자동화해야 하면 std04의 `tools/check_browser_step*.py`처럼 표준 라이브러리로 CDP를 직접 다룬다.
