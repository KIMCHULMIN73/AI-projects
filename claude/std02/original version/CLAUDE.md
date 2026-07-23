# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this folder.

## Folder overview

`original version/` is the **completed, frozen original** of the "Daily To-Do Manager" (데일리 할 일 관리 앱), built incrementally through 6 stages — the 5 originally planned in the prompts doc, plus a 6th added later at the user's request:

- **`todo_app_PRD.docx`** — the Product Requirements Document (v1.0): a single-user, browser-only to-do list with add/edit/delete, work/personal/study categorization, completion tracking, and a progress bar.
- **`todo_app_claude_code_prompts.md`** — the PRD translated into 5 sequential, copy-pasteable Claude Code prompts meant to be run **in order**, each building on the previous stage's output. This document only covers stages 1–5; stage 6 (scheduling) is not in it.
- **`index.html`, `style.css`, `app.js`** — the app itself. **All 6 stages are implemented** — see Build sequence below for what each stage added.

There is no build, lint, or test tooling here because the target app itself has none by design (see constraints below).

## Preservation policy

This folder is the finished original that `../new version/` builds upon. **Do not add new features here** — new development happens in `../new version/`. Only touch this folder for bug fixes to the original app, and keep any fix within the constraints below.

## Hard constraints on the app (from the PRD)

Any code change in this folder must honor these, since they're the PRD's core design decisions, not incidental defaults:

- Pure **HTML5 + CSS3 + vanilla JavaScript (ES6+)** only — no frameworks, no external libraries, no build tools/bundlers/npm.
- Must run by **opening `index.html` directly in a browser** — no dev server, no backend, no DB.
- All persistence via **`localStorage`** only, under the fixed key `todos-app-v1`, storing a JSON array of to-do objects. Every localStorage access must be wrapped in try/catch so a parse failure or storage error never crashes the app (fall back to an empty list).
- Exactly three code files: `index.html`, `style.css`, `app.js`.
- Data model per to-do item: `{ id, title, category: 'work' | 'personal' | 'study', completed, createdAt, startTime, durationMinutes }` — `startTime`/`durationMinutes` are stage-6 additions (`'HH:MM'` string / minutes number) and are `null` together when the item is unscheduled.

## Build sequence

`todo_app_claude_code_prompts.md` defines stages 1–5; stage 6 was requested directly by the user afterward and follows the same one-stage-at-a-time pattern. Each stage assumes the previous one is already implemented:

1. ✅ **Done** — Static layout/skeleton (header + progress placeholder, input area, filter tabs, empty list container) — no data logic yet.
2. ✅ **Done** — Data layer: `loadTodos()`/`saveTodos()` against `localStorage` (key `todos-app-v1`, try/catch-guarded, empty-array fallback), an in-memory `todos` array, and a bare-bones `renderTodos()` for verification. `init()` runs `loadTodos()` → `renderTodos()` on page load.
3. ✅ **Done** — Core CRUD wired to the UI: `addTodo()` (Enter or button, blank/whitespace-only titles rejected and focus kept, `id`/`createdAt` via `Date.now()`), edit mode via `startEdit()`/`commitEdit()`/`cancelEdit()` (double-click title or ✎ button to enter edit; Enter/Esc keyboard shortcuts **and** visible 저장/취소 buttons both commit/cancel — the buttons were added after the user pointed out the edit row had no explicit ones), and `deleteTodo()` (✕ button). `renderTodos()` builds real DOM rows (`createViewRow()`/`createEditRow()`). Every mutation calls `saveTodos()` + `renderTodos()` together.
4. ✅ **Done** — Completion toggle: the row checkbox (`change` → `toggleComplete()`, saves + re-renders immediately), completed items get a `.completed` class → strikethrough + muted title color. Category filter: `currentFilter` state + `setFilter()` wired to the filter tab clicks (toggles `.active`), `renderTodos()` filters a *display-only* `visibleTodos` list from the full `todos` array so filtering never mutates or drops data, and add/edit/delete continue to operate on the full array regardless of the active filter.
5. ✅ **Done** — `updateProgress()` computes the bar width and "완료 n / 전체 m" text from the *full* `todos` array (never the filtered `visibleTodos`), called at the end of every `renderTodos()`. Empty state (`todos.length === 0`) renders a "할 일을 추가해보세요" `.empty-message` instead of the list and shows 0%. `clearCompleted()` + the "완료 항목 지우기" button (`#clearCompletedBtn`) remove all completed items in one action (F-8, Should).
6. ✅ **Done** — Scheduling extension (user-requested, not in `todo_app_claude_code_prompts.md`): each to-do can optionally carry a start time (`startTime`, `'HH:MM'`) and a duration in minutes (`durationMinutes`) via new inputs in both the add form and the edit row; both must be set together or the item is treated as unscheduled. All schedules are implicitly "today" (single-day app, no date picker).
   - `getScheduleStatus(todo, now)` is the single source of truth for a to-do's time-based state: `'none'` (no schedule → gray `.status-none` box), `'upcoming'` (before start → normal look), `'warning'` (between start and start+duration → blue blinking checkbox via `.status-warning` + the `blink-warning` CSS animation), `'overdue'` (past start+duration → red checkbox forced `disabled`, title gets red double-strikethrough via `.status-overdue`).
   - `enforceOverdueRules()` runs at the top of every `renderTodos()` and forces `completed = false` on any item that has gone overdue — an overdue item can never stay checked off.
   - A `setInterval` re-runs `renderTodos()` every second (skipped while `editingId !== null`, so in-progress edits aren't interrupted) so status transitions and the blink animation happen without user interaction.
   - `findOverlappingTodo(startTime, durationMinutes, excludeId)` checks a new/edited time range against every other scheduled item; `addTodo()` and `commitEdit()` both call it and **block** the add/edit with `alert('다른 업무와 시간 중복\n"<title>"과(와) 시간이 겹칩니다.')` naming the conflicting item — the user explicitly chose "block" over "warn and allow", so don't silently permit overlapping schedules.
   - Keep `getScheduleStatus()` as the only place that interprets the clock — new rendering or logic should call it rather than re-deriving status inline.

All 6 stages are implemented and there is no stage 7 in any source document. Further improvement of the app happens in `../new version/`, not here.

## Verification

There are no automated tests; verification is manual in-browser. The single most important scenario is that data survives a page refresh (F5) — called out in the PRD as the core requirement. For stage 6, also verify: a scheduled item's checkbox turns blue and blinks once its start time passes, turns red and locks once the duration elapses (and forces itself back to incomplete if it was checked), an unscheduled item's box is gray, and adding/editing a time range that overlaps another item pops the "다른 업무와 시간 중복" alert and is not saved.

## Repository context

The git root is three levels up (`../../..`, `AI-projects`). See `../CLAUDE.md` for how this folder relates to `../new version/` and the rest of the repository.
