# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

`std02` is a "Daily To-Do Manager" (데일리 할 일 관리 앱) project, split into two versions:

- **`original version/`** — the completed original app, built through 6 incremental stages (static layout → data layer → CRUD → completion/filter → progress → scheduling). It also holds the source documents (`todo_app_PRD.docx`, `todo_app_claude_code_prompts.md`). This version is **frozen**: bug fixes only, no new features. See `original version/CLAUDE.md` for the full build history, constraints, and verification checklist.
- **`new version/`** — the next iteration, to be developed on top of the original. **Currently empty (not started).** See `new version/CLAUDE.md` for the starting-point guidance and inherited constraints.

Always work inside the relevant subfolder and consult its `CLAUDE.md` — there is no code at this top level.

## Constraints common to both versions

- Pure **HTML5 + CSS3 + vanilla JavaScript (ES6+)** only — no frameworks, no external libraries, no build tools/bundlers/npm.
- Must run by **opening `index.html` directly in a browser** — no dev server, no backend, no DB.
- All persistence via **`localStorage`** only, with every access try/catch-guarded so a parse/storage failure never crashes the app. The original owns the storage key `todos-app-v1`; the new version must use a different key if its data model diverges.
- No build, lint, or test tooling by design; verification is manual in-browser, with data surviving a page refresh (F5) as the core scenario.

## Repository context

The git root is two levels up (`../..`, `AI-projects`), which also contains an unrelated `chatGPT/` directory of static HTML pages and a sibling `claude/std01/` project (a handwritten digit recognizer with its own hierarchical `CLAUDE.md` files) — don't assume shared code or tooling with those.
