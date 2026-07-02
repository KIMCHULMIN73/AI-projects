# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Browser-based handwritten digit recognizer — a counterpart to the Tkinter desktop app in `../desktop version`, with identical functionality: draw a digit (0-9) on a black canvas, click "Recognize" to get a prediction and confidence, click "Clear" to reset.

This is a **fully static, client-side-only** site — `index.html` + `style.css` + `script.js` + `weights.json`. There is no backend and no build step; inference (the MLP forward pass) runs entirely in the browser in plain JS. This was a deliberate choice over a server-backed API, so the app can be opened/served as static files with no Python process running.

## Common commands

```bash
# Serve locally (any static file server works)
python3 -m http.server 8000
# then open http://localhost:8000/ in a browser

# Regenerate weights.json after the desktop model is retrained
"../desktop version/.venv/bin/python3" scripts/export_model.py
```

There is no test suite, linter, or build step. `index.html` can technically be opened directly via `file://`, but prefer serving it over HTTP — `fetch("weights.json")` may be blocked under `file://` in some browsers.

## Architecture

- **`scripts/export_model.py`** — a one-off Python script (not part of the runtime app) that loads `../desktop version/digit_model.joblib` and dumps its learned parameters (`coefs_`, `intercepts_`, `classes_`, `activation`, `out_activation_`) to `weights.json`, rounded to 6 decimals to keep the file smaller. Run this any time the desktop model changes; `weights.json` must be regenerated and committed, it is not derived at runtime.

- **`script.js`** — reimplements, in plain JS, everything `../desktop version/digit_recognizer_gui.py` does in Python:
  - Canvas drawing (mouse + touch events) — same brush size (`BRUSH_RADIUS = 10`), same black background / white strokes.
  - `preprocess()` — mirrors the desktop version's crop-to-bounding-box → pad-to-square → add ~20% margin → resize-to-28x28 → normalize-to-`[0,1]` pipeline. The resize uses the canvas's built-in bilinear/bicubic scaling (`imageSmoothingQuality: "high"`) rather than PIL's LANCZOS filter used on desktop; this is a known, minor approximation — in practice predictions match the desktop version closely (verified: a drawn "1" and a drawn "0" both scored 100% confidence, matching what the same shapes yield on desktop).
  - `predict()` — replicates `MLPClassifier`'s forward pass by hand (linear layer → activation, repeated per layer, then softmax on the output layer) using the weights from `weights.json`. This is a generic implementation driven by `hidden_activation`/`out_activation` from the JSON, not hardcoded to relu/softmax, so it stays correct if the desktop model is retrained with different settings.
  - No ML framework (TensorFlow.js, ONNX Runtime Web, etc.) is used — the forward pass is small enough (784→256→128→10) that hand-written loops are simpler and dependency-free.

- **`weights.json`** — generated data, not hand-edited. ~4.5MB; loaded once via `fetch` on page load into the global `model` object.

## Known gaps vs. the desktop version

- Digit preprocessing resize algorithm differs slightly (browser bilinear/bicubic vs. PIL LANCZOS) — acceptable in practice, but if predictions seem to diverge from desktop for specific inputs, this is the first place to look.
- No automated test/CI harness exists; verification has been manual (drawing shapes and checking `script.js`'s `preprocess()`/`predict()` output via a headless-browser smoke test during development, not checked into the repo).
