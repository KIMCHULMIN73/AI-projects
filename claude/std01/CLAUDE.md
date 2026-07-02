# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

`std01` is a handwritten digit recognizer project, split into two independent implementations:

- **`desktop version/`** — a working Tkinter GUI app backed by a scikit-learn MLP classifier trained on MNIST. See `desktop version/CLAUDE.md` for commands and architecture.
- **`web version/`** — a static, client-side-only browser app with the same functionality (draw/recognize/clear), running the same model's weights via a hand-written JS forward pass — no backend. See `web version/CLAUDE.md`.

The two versions share one source of truth for the model: `desktop version/digit_model.joblib` is trained first, then `web version/scripts/export_model.py` exports its weights into `web version/weights.json` for the browser version to consume.

There is no code, environment, or build step at this top level — always work inside the relevant subfolder and consult its `CLAUDE.md`.

Note: the git repository root is one level up at `../..` (`AI-projects`), which also contains an unrelated `chatGPT/` directory of static HTML pages.
