# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this folder.

## Folder overview 

`new version/` is the **next iteration** of the "Daily To-Do Manager" (데일리 할 일 관리 앱), to be built on top of the completed app in `../original version/`.

**Current status: empty — development has not started yet.** This CLAUDE.md is a placeholder; update it as the new version's requirements and implementation take shape.

## Starting point

When development begins, start from the original's finished state — either copy `index.html`, `style.css`, `app.js` from `../original version/` or use them as the reference implementation. Read `../original version/CLAUDE.md` first: it documents the full 6-stage build (data layer, CRUD, filters, progress, and the stage-6 scheduling system with `getScheduleStatus()` as the single clock-interpreting function).

## Inherited constraints (default assumptions)

Unless the user explicitly changes them for the new version, carry over the original's hard constraints:

- Pure **HTML5 + CSS3 + vanilla JavaScript (ES6+)** — no frameworks, no external libraries, no build tools.
- Runs by **opening `index.html` directly in a browser** — no server, no backend, no DB.
- Persistence via **`localStorage`** only, every access try/catch-guarded with an empty-array fallback. If the new version changes the data model, use a **new storage key** (the original owns `todos-app-v1`) so the two versions don't corrupt each other's data when opened in the same browser.
- No automated test tooling; verification is manual in-browser, with F5 data persistence as the core scenario.

## Maintaining this file

As features land in the new version, document here: what differs from the original, the data model and storage key actually used, and a build/feature log in the same per-stage style as `../original version/CLAUDE.md`.
