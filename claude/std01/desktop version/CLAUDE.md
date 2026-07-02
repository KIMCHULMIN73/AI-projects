# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

The desktop version of the handwritten digit recognizer for Raspberry Pi OS: a scikit-learn MLP classifier trained on MNIST, exposed through a Tkinter GUI where the user draws a digit with mouse/touchscreen and gets a prediction with confidence.

This directory is a subfolder of the `std01` project, which also contains a `../web version` folder for a browser-based counterpart (not yet implemented — see its `CLAUDE.md`). The two versions are independent; nothing in this folder should assume the web version exists.

The git repository root is three levels up (`../../..`, `AI-projects`), which also contains an unrelated `chatGPT/` directory of static HTML pages.

## Environment

- Python 3.13, dependencies live in the local `.venv/` (activate with `.venv/bin/python3` or `source .venv/bin/activate`).
- No `requirements.txt`/`pyproject.toml` exists — dependencies are installed directly into `.venv`. Key packages: `scikit-learn`, `numpy`, `joblib`, `pillow`. If adding dependencies, install into `.venv` with `.venv/bin/pip install <pkg>`.

## Common commands

Run all commands from this directory (`desktop version/`):

```bash
# Train the model (downloads MNIST on first run, saves digit_model.joblib)
.venv/bin/python3 train_model.py

# Run the GUI (requires digit_model.joblib to already exist)
.venv/bin/python3 digit_recognizer_gui.py

# Launch via the wrapper script (auto-trains the model if missing)
./run_digit_recognizer.sh
```

There is no test suite, linter, or build step configured for this project.

`event_test.py` is a standalone scratch script for logging raw Tkinter canvas mouse events (press/motion/release) — useful for debugging touchscreen/mouse input behavior, not part of the app.

## Architecture

Two-stage pipeline, split across two scripts that share `digit_model.joblib` as the interface:

1. **`train_model.py`** — fetches `mnist_784` via `sklearn.datasets.fetch_openml`, trains an `MLPClassifier` (hidden layers 256/128), evaluates accuracy, and serializes the fitted model to `digit_model.joblib` with `joblib.dump`. Run once (or whenever the model needs retraining); not invoked automatically by the GUI unless the model file is missing (see `run_digit_recognizer.sh`). `MODEL_PATH` here is a relative path resolved against the current working directory, not the script's directory — always run it from this folder (the wrapper script already `cd`s here first).

2. **`digit_recognizer_gui.py`** — Tkinter app (`DigitRecognizerApp`) that loads `digit_model.joblib` with `joblib.load` at startup and fails fast with a dialog if it's missing. Drawing on the canvas simultaneously renders to a Tkinter `Canvas` (for display) and a parallel in-memory PIL `Image` (for inference) — both must stay in sync in `on_paint`. On "Recognize", `preprocess()` converts the drawn image into a 28x28 normalized vector matching MNIST's format: crop to bounding box → pad to square → add ~20% margin → resize to 28x28 with Lanczos → flatten and normalize to `[0, 1]`. This preprocessing must mirror MNIST's centered/padded digit convention or predictions degrade. (A future web version implementing browser-side inference would need to replicate this same preprocessing.)

`MODEL_PATH` in `digit_recognizer_gui.py` is resolved relative to the script's own directory (not CWD), so the GUI can be launched from anywhere as long as `digit_model.joblib` sits alongside it.

## Deployment (Raspberry Pi)

`DigitRecognizer.desktop` is a Linux desktop entry pointing at the absolute path `/home/kimchulmin/git-directory/AI-projects/claude/std01/desktop version/run_digit_recognizer.sh` (quoted in the `Exec=` line because the path contains a space) — if this folder is relocated or renamed, update that `Exec=` path to match.
