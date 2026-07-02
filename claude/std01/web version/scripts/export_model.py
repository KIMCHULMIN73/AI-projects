"""
Export the trained desktop MLPClassifier (../desktop version/digit_model.joblib)
into a JSON weight file that script.js loads and runs client-side, so the web
version needs no server-side inference.

Run once, whenever the desktop model is (re)trained:
    ../desktop version/.venv/bin/python3 scripts/export_model.py
"""

import json
import os

import joblib
import numpy as np

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESKTOP_MODEL_PATH = os.path.join(WEB_DIR, "..", "desktop version", "digit_model.joblib")
OUTPUT_PATH = os.path.join(WEB_DIR, "weights.json")

# Weights are rounded to keep weights.json a reasonable size; this has a
# negligible effect on predictions.
ROUND_DECIMALS = 6


def main():
    clf = joblib.load(DESKTOP_MODEL_PATH)

    data = {
        "hidden_activation": clf.activation,
        "out_activation": clf.out_activation_,
        "classes": clf.classes_.tolist(),
        "layers": [
            {
                "weights": np.round(w, ROUND_DECIMALS).tolist(),
                "biases": np.round(b, ROUND_DECIMALS).tolist(),
            }
            for w, b in zip(clf.coefs_, clf.intercepts_)
        ],
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(data, f)

    size_kb = os.path.getsize(OUTPUT_PATH) / 1024
    print(f"Exported {len(data['layers'])} layers to {OUTPUT_PATH} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
